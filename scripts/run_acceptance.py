#!/usr/bin/env python3
"""scripts/run_acceptance.py — Offline native analyser acceptance harness.

Drives HueSync's production AudioCanonicalizer + PcmAudioPipelineV2 (or
CavaCoreAudioPipeline) against a local audio file decoded by ffmpeg, and records
every analyser snapshot to a CSV for offline acceptance analysis.

No DSP logic is duplicated here.  The production analysis code is called
directly: AudioCanonicalizer.push() + pipeline._process_canonical_frame().
The pipeline worker thread is bypassed; the DSP path is unchanged.

Chunk size: 441 source samples (10 ms at 44100 Hz).  After soxr resampling to
48 kHz, each chunk produces ~480 canonical samples.  The harness then re-chunks
canonical output into exact HOP-sample (480-sample) blocks via _CanonicalRechunker
before each _process_canonical_frame() call.  This guarantees at most one STFT
frame per call after warmup, so every snapshot is captured without loss.

After the STFT warmup period (~50 ms), the CSV contains approximately one row
per STFT frame (≈100 Hz rate, ~296 rows per 3 seconds of audio).

Timestamps are derived from the 480-sample block's canonical sample_pos
(canonical sample counter at 48 kHz), never from wall clock.  Given the same
input file and the same git commit the output is deterministic.

Usage:
    python scripts/run_acceptance.py \\
        --input   <audio-file>       \\
        --output  <csv-file>         \\
        [--backend  v2|cavacore]     \\
        [--start    <seconds>]       \\
        [--duration <seconds>]       \\
        [--ffmpeg   <path>]

Decode contract:
    ffmpeg → S16LE  44100 Hz  stereo  (pipe:1)

Outputs:
    <csv-file>            — one row per AudioFeatures snapshot (post-warmup)
    <csv-file>.meta.json  — reproducibility / provenance record

A/B comparison:
    Run once with --backend v2 and once with --backend cavacore against the
    same input file.  Compare bar columns across both CSVs.  The "backend"
    column in each CSV identifies which pipeline produced the row.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from huesync.canonicalizer import (  # noqa: E402
    AnalysisPcmFrame,
    AudioCanonicalizer,
    CanonicalData,
    DataResult,
    DecodedSourceFrame,
    EndOfStream,
)
from huesync.pcm_source import WINDOW_SIZE  # noqa: E402
from huesync.spectrum_engine import ENGINES, make_spectrum_engine  # noqa: E402
from huesync.sync_engine import CanonicalAnalysisPipeline  # noqa: E402

# Suppress the per-frame diagnostic INFO log that fires every 50 frames.
# This log was added as a temporary field-debugging aid and is not structural.
# The harness suppresses it at WARNING so stdout stays clean.  Production
# runtime behavior is unchanged (the harness never runs on the LXC).
logging.getLogger("huesync.sync_engine").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_RATE: int = 44100           # AirPlay / shairport-sync contract rate
CANONICAL_RATE: int = AudioCanonicalizer.TARGET_RATE  # 48000 Hz

# 441 source samples = exactly 10 ms at 44100 Hz.
# After soxr resampling (×160/147), output ≈ 480 canonical samples = one hop.
CHUNK_SAMPLES: int = 441
CHUNK_BYTES: int = CHUNK_SAMPLES * 2 * 2  # stereo × 2 bytes (S16LE)

# Canonical hop size in samples.  Matches PcmStft(48000).hop = round(48000 × 0.010).
# _CanonicalRechunker re-chunks every AudioCanonicalizer batch to this size so
# _process_canonical_frame() processes at most one STFT frame per call.
HOP: int = 480

# Analyser defaults that match HueSync production coupling/analyser model defaults.
_DEFAULT_BARS: int = 30
_DEFAULT_LOWER_HZ: int = 50
_DEFAULT_UPPER_HZ: int = 12000
_DEFAULT_ONSET_METHOD: str = "combined"
_DEFAULT_ONSET_DELTA: float = 0.1
_DEFAULT_ONSET_ALPHA: float = 0.9
_DEFAULT_SUPERFLUX_MU: int = 3
_DEFAULT_SUPERFLUX_LAG: int = 2
_DEFAULT_BASS_HZ: int = 250
_DEFAULT_MID_HZ: int = 2000
_DEFAULT_EXERTION_CLIP: float = 3.0

# CSV columns — order is fixed for reproducibility.
_BAR_COLS = [f"bar_{i:02d}" for i in range(_DEFAULT_BARS)]
CSV_COLUMNS: list[str] = (
    ["t_s", "backend", "effective_backend"]
    + _BAR_COLS
    + [
        "bass",
        "mid",
        "full",
        "centroid",
        "onset",
        "onset_strength",
        "onset_bass",
        "onset_mid",
        "onset_treble",
        "onset_bass_strength",
        "onset_mid_strength",
        "onset_treble_strength",
        "relative_exertion",
        # Pipeline internal conditioning — exposed via CanonicalAnalysisPipeline properties.
        # v2_peak_ema and v2_bar_smooth are populated for v2 engine; 0.0 otherwise.
        "peak_ema",         # v2_peak_ema: global reference at this moment
        "bars_smooth_mean", # mean(v2_bar_smooth) (explicit label)
        "bars_smooth_max",  # max(v2_bar_smooth)
    ]
)

# raw_mag_mean and bar_float_max are local variables inside _process_canonical_frame()
# and are not stored as instance state.  They cannot be captured without modifying
# production source code.  They are omitted from version 1 of this harness.

# Common locations to search when ffmpeg is not in PATH.
_FFMPEG_FALLBACKS: list[str] = [
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    r"/mnt/c/Program Files/dBpoweramp/ffmpeg-lgpl.exe",
    r"/mnt/c/Program Files/ffmpeg/bin/ffmpeg.exe",
    r"/mnt/c/ffmpeg/bin/ffmpeg.exe",
    r"/mnt/c/ProgramData/chocolatey/bin/ffmpeg.exe",
]


# ---------------------------------------------------------------------------
# Offline source stub
# ---------------------------------------------------------------------------


class _OfflineSource:
    """Stub satisfying PcmAudioPipelineV2's duck-typed source contract.

    The harness bypasses _run() and calls _process_canonical_frame() directly,
    so read() is never invoked.  running=False so the TemporarilyNoData branch
    in _run() would clear _latest — irrelevant here, but documented for clarity.
    """

    @property
    def running(self) -> bool:
        return False

    def read(self) -> None:
        raise RuntimeError("_OfflineSource.read() must not be called in offline mode")


# ---------------------------------------------------------------------------
# Canonical re-chunker
# ---------------------------------------------------------------------------


class _CanonicalRechunker:
    """Re-chunks variable-length AnalysisPcmFrame batches into exact HOP-sample blocks.

    AudioCanonicalizer emits batches of ~893 canonical samples for each 441-source-
    sample chunk (≈1.86 STFT hops per batch).  _process_canonical_frame() iterates
    over all STFT frames from one batch and overwrites _latest each iteration;
    latest() then only returns the final snapshot per batch, losing intermediates.

    Feeding exactly HOP samples per _process_canonical_frame() call guarantees at
    most one new STFT frame after warmup.  Every snapshot is captured immediately
    after each call, with no intermediate overwrites.
    """

    def __init__(self) -> None:
        self._buf: np.ndarray | None = None   # writeable (n, 2) float32 accumulator
        self._pos: int = 0                    # canonical sample_pos at buffer start
        self._epoch_id: str | None = None
        self._source_id: str | None = None
        self._over_range_acc: bool = False    # OR-accumulated across buffered frames

    def push(self, frame: AnalysisPcmFrame) -> list[AnalysisPcmFrame]:
        """Accept one variable-length canonical frame; emit zero or more HOP-sized frames."""
        new_samples = frame.samples.copy()  # writeable copy for accumulation
        if self._buf is None:
            self._buf = new_samples
            self._pos = frame.sample_pos
            self._epoch_id = frame.epoch_id
            self._source_id = frame.source_id
        else:
            self._buf = np.concatenate([self._buf, new_samples], axis=0)
        self._over_range_acc = self._over_range_acc or frame.over_range

        out: list[AnalysisPcmFrame] = []
        while len(self._buf) >= HOP:
            out.append(AnalysisPcmFrame(
                samples=self._buf[:HOP],
                sample_pos=self._pos,
                epoch_id=self._epoch_id,
                source_id=self._source_id,
                over_range=self._over_range_acc,
            ))
            self._pos += HOP
            self._buf = self._buf[HOP:]
            self._over_range_acc = False

        if len(self._buf) == 0:
            self._buf = None
        return out

    def flush(self) -> list[AnalysisPcmFrame]:
        """Emit remaining samples as a partial block (end-of-stream tail)."""
        if self._buf is None or len(self._buf) == 0:
            return []
        out = [AnalysisPcmFrame(
            samples=self._buf,
            sample_pos=self._pos,
            epoch_id=self._epoch_id,
            source_id=self._source_id,
            over_range=self._over_range_acc,
        )]
        self._buf = None
        self._over_range_acc = False
        return out


# ---------------------------------------------------------------------------
# ffmpeg discovery
# ---------------------------------------------------------------------------


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """Return a usable ffmpeg executable path, or None."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates += ["ffmpeg", "ffmpeg.exe"]
    candidates += _FFMPEG_FALLBACKS

    for cand in candidates:
        path = shutil.which(cand) or (cand if Path(cand).is_file() else None)
        if not path:
            continue
        try:
            r = subprocess.run(
                [path, "-version"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return path
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def ffmpeg_version(ffmpeg: str) -> str:
    """Return the ffmpeg version string (e.g. '6.1.1'), or 'unknown'."""
    try:
        r = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout:
            parts = r.stdout.splitlines()[0].split()
            # First line: "ffmpeg version X.Y.Z ..."
            if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
                return parts[2]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Audio decode
# ---------------------------------------------------------------------------


def _to_ffmpeg_path(ffmpeg: str, path: Path) -> str:
    """Return a path string that *ffmpeg* can open.

    On WSL, Windows executables (*.exe, path under /mnt/c/...) cannot
    interpret Linux absolute paths.  wslpath converts them to Windows paths.
    Linux ffmpeg gets the absolute Linux path unchanged.
    """
    is_windows_exe = str(ffmpeg).lower().endswith(".exe") or "/mnt/" in ffmpeg
    if is_windows_exe:
        try:
            r = subprocess.run(
                ["wslpath", "-w", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return str(path)


def decode_audio(
    ffmpeg: str,
    input_path: Path,
    start: float | None = None,
    duration: float | None = None,
) -> bytes:
    """Decode *input_path* to raw S16LE 44100 Hz stereo PCM via ffmpeg.

    Returns all PCM bytes buffered in memory.  Deterministic chunking in
    analyse_pcm() then slices this buffer at fixed CHUNK_BYTES intervals
    regardless of ffmpeg's internal buffering behaviour.

    On WSL with a Windows ffmpeg.exe, input_path is converted to a Windows
    path via wslpath so the Windows process can open it.
    """
    ffmpeg_input_path = _to_ffmpeg_path(ffmpeg, input_path)
    cmd: list[str] = [ffmpeg]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", ffmpeg_input_path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vn",          # discard video streams
        "-f", "s16le",
        "-ar", str(SOURCE_RATE),
        "-ac", "2",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}):\n"
            + result.stderr.decode(errors="replace")[-1000:]
        )
    if not result.stdout:
        raise RuntimeError("ffmpeg produced no PCM output — check input file and arguments")
    return result.stdout


# ---------------------------------------------------------------------------
# Core analysis loop
# ---------------------------------------------------------------------------


def analyse_pcm(
    raw_pcm: bytes,
    backend: str = "v2",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run raw S16LE 44100 Hz stereo PCM through the production analysis path.

    Calls AudioCanonicalizer.push() and CanonicalAnalysisPipeline.feed()
    directly.  No DSP is duplicated; the worker thread is simply not started.

    The `backend` parameter selects the spectrum engine via the ENGINES registry.
    Any engine registered in spectrum_engine.ENGINES is accepted.

    AudioCanonicalizer emits variable-length batches (~893 canonical samples per
    441-source-sample chunk).  _CanonicalRechunker re-chunks these into exact
    HOP-sample (480-sample) blocks before each feed() call, guaranteeing at most
    one STFT frame per call and one CSV row per STFT frame.

    Args:
        raw_pcm: Complete S16LE 44100 Hz stereo PCM bytes (any length).
        backend: Engine ID from spectrum_engine.ENGINES (e.g. "v2", "cavacore").

    Returns:
        rows:  list of dicts, one per AudioFeatures snapshot (post-STFT-warmup).
        info:  pipeline configuration dict (for metadata JSON).
    """
    if backend not in ENGINES:
        raise KeyError(
            f"Unknown spectrum engine {backend!r}; valid: {sorted(ENGINES.keys())}"
        )
    engine = make_spectrum_engine(
        backend,
        n_bars=_DEFAULT_BARS,
        lower_hz=float(_DEFAULT_LOWER_HZ),
        upper_hz=float(_DEFAULT_UPPER_HZ),
    )
    pipeline = CanonicalAnalysisPipeline(
        source=_OfflineSource(),
        engine=engine,
        onset_method=_DEFAULT_ONSET_METHOD,
        onset_delta=_DEFAULT_ONSET_DELTA,
        onset_alpha=_DEFAULT_ONSET_ALPHA,
        superflux_mu=_DEFAULT_SUPERFLUX_MU,
        superflux_lag=_DEFAULT_SUPERFLUX_LAG,
        bass_hz=_DEFAULT_BASS_HZ,
        mid_hz=_DEFAULT_MID_HZ,
    )

    assert pipeline.hop == HOP, (
        f"Production hop {pipeline.hop} != harness HOP {HOP} — update HOP constant"
    )
    canonicalizer = AudioCanonicalizer()
    rechunker = _CanonicalRechunker()

    rows: list[dict[str, Any]] = []
    violations: list[str] = []
    source_pos: int = 0  # source-rate stereo frame counter
    _last_rec: list[object] = []  # capture last PublicationRecord for metadata

    def _capture_row(features: object, sample_pos: int, rec: object | None = None) -> None:
        """Record one AudioFeatures snapshot as a CSV row."""
        t_s = sample_pos / CANONICAL_RATE
        row: dict[str, Any] = {
            "t_s": round(t_s, 6),
            "backend": backend,
            "effective_backend": (
                getattr(rec, "effective_engine_id", backend) if rec else backend
            ),
        }
        for i, v in enumerate(features.bars):  # type: ignore[union-attr]
            row[f"bar_{i:02d}"] = round(float(v), 6)
        row["bass"] = round(float(features.bass), 6)  # type: ignore[union-attr]
        row["mid"] = round(float(features.mid), 6)  # type: ignore[union-attr]
        row["full"] = round(float(features.full), 6)  # type: ignore[union-attr]
        row["centroid"] = round(float(features.centroid), 6)  # type: ignore[union-attr]
        row["onset"] = int(features.onset)  # type: ignore[union-attr]
        row["onset_strength"] = round(float(features.onset_strength), 6)  # type: ignore[union-attr]
        row["onset_bass"] = int(features.onset_bass)  # type: ignore[union-attr]
        row["onset_mid"] = int(features.onset_mid)  # type: ignore[union-attr]
        row["onset_treble"] = int(features.onset_treble)  # type: ignore[union-attr]
        row["onset_bass_strength"] = round(float(features.onset_bass_strength), 6)  # type: ignore[union-attr]
        row["onset_mid_strength"] = round(float(features.onset_mid_strength), 6)  # type: ignore[union-attr]
        row["onset_treble_strength"] = round(float(features.onset_treble_strength), 6)  # type: ignore[union-attr]
        row["relative_exertion"] = round(float(features.relative_exertion), 6)  # type: ignore[union-attr]

        # Pipeline internal conditioning — available via CanonicalAnalysisPipeline properties.
        peak_ema = pipeline.v2_peak_ema
        bar_smooth = pipeline.v2_bar_smooth
        row["peak_ema"] = round(float(peak_ema), 6) if peak_ema is not None else 0.0
        if bar_smooth is not None:
            row["bars_smooth_mean"] = round(sum(bar_smooth) / len(bar_smooth), 6)
            row["bars_smooth_max"] = round(max(bar_smooth), 6)
        else:
            row["bars_smooth_mean"] = 0.0
            row["bars_smooth_max"] = 0.0

        # Hard correctness invariants — reported but do not abort analysis.
        for i in range(_DEFAULT_BARS):
            v = row[f"bar_{i:02d}"]
            if not math.isfinite(v):
                violations.append(f"t={t_s:.3f} bar_{i:02d} non-finite: {v}")
            elif v < -1e-6 or v > 1.0 + 1e-6:
                violations.append(f"t={t_s:.3f} bar_{i:02d} out of [0,1]: {v:.6f}")
        for field in ("bass", "mid", "full", "centroid"):
            v = row[field]
            if not math.isfinite(v):
                violations.append(f"t={t_s:.3f} {field} non-finite: {v}")

        rows.append(row)
        if rec is not None:
            _last_rec.clear()
            _last_rec.append(rec)

    def _push_and_capture(hop_frame: AnalysisPcmFrame) -> None:
        """Feed one HOP-sized canonical frame; capture snapshots from returned records."""
        for rec in pipeline.feed(hop_frame):
            _capture_row(rec.features, rec.sample_pos, rec)

    def _feed_canonical(cresult: CanonicalData) -> None:
        """Re-chunk canonical batch into HOP blocks and capture each snapshot."""
        for hop_frame in rechunker.push(cresult.frame):
            _push_and_capture(hop_frame)

    def _process_chunk(buf: bytes, src_pos: int) -> None:
        aligned = len(buf) - (len(buf) % 4)  # align to stereo S16LE frame boundary
        if aligned < 4:
            return
        s16 = np.frombuffer(buf[:aligned], dtype=np.int16)
        stereo = s16.reshape(-1, 2).astype(np.float32) / 32768.0
        frame = DecodedSourceFrame(
            samples=np.ascontiguousarray(stereo),
            sample_rate=SOURCE_RATE,
            channels=2,
            source_id="offline:harness",
            source_sample_pos=src_pos,
            over_range=bool(np.any(np.abs(stereo) >= 1.0)),
            wall_ns=None,
        )
        for cresult in canonicalizer.push(DataResult(frame=frame)):
            if isinstance(cresult, CanonicalData):
                _feed_canonical(cresult)

    # Process full + partial chunks.
    for offset in range(0, len(raw_pcm), CHUNK_BYTES):
        chunk = raw_pcm[offset : offset + CHUNK_BYTES]
        _process_chunk(chunk, source_pos)
        source_pos += len(chunk) // 4  # 4 bytes per stereo S16LE frame

    # Drain soxr's internal buffer at end of stream.
    for cresult in canonicalizer.push(EndOfStream()):
        if isinstance(cresult, CanonicalData):
            _feed_canonical(cresult)

    # Flush rechunker's partial-block tail (< HOP samples remaining after EOS drain).
    for hop_frame in rechunker.flush():
        _push_and_capture(hop_frame)

    # EOS flush: drain any carry buffer (cavacore: up to 479 samples; CAP: no-op for v2).
    for rec in pipeline.end_of_stream():
        _capture_row(rec.features, rec.sample_pos, rec)

    hop = pipeline.hop
    last_rec = _last_rec[0] if _last_rec else None
    effective_processor_ids = (
        list(getattr(last_rec, "effective_processor_ids", []))
        if last_rec else []
    )
    info: dict[str, Any] = {
        "backend": backend,
        "effective_backend": pipeline.effective_spectrum_backend,
        "effective_processor_ids": effective_processor_ids,
        "fft_size": WINDOW_SIZE,
        "onset_fft_size": WINDOW_SIZE,
        "hop": hop,
        "window": "hamming",
        "canonical_rate": CANONICAL_RATE,
        "n_bars": _DEFAULT_BARS,
        "lower_hz": _DEFAULT_LOWER_HZ,
        "upper_hz": _DEFAULT_UPPER_HZ,
        "onset_method": _DEFAULT_ONSET_METHOD,
        "onset_delta": _DEFAULT_ONSET_DELTA,
        "onset_alpha": _DEFAULT_ONSET_ALPHA,
        "superflux_mu": _DEFAULT_SUPERFLUX_MU,
        "superflux_lag": _DEFAULT_SUPERFLUX_LAG,
        "bass_hz": _DEFAULT_BASS_HZ,
        "mid_hz": _DEFAULT_MID_HZ,
        "exertion_clip": _DEFAULT_EXERTION_CLIP,
        "violations": violations,
    }
    return rows, info


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_csv(output_path: Path, rows: list[dict[str, Any]]) -> None:
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_meta(
    meta_path: Path,
    *,
    input_name: str,
    sha256: str,
    git_commit: str,
    start: float | None,
    duration: float | None,
    chunk_samples: int,
    pipeline_info: dict[str, Any],
    n_rows: int,
    ffmpeg_version: str | None = None,
) -> None:
    hop = pipeline_info["hop"]
    canonical_rate = pipeline_info["canonical_rate"]
    analysed_s = round(n_rows * hop / canonical_rate, 3) if n_rows > 0 else 0.0

    meta: dict[str, Any] = {
        "source": input_name,
        "sha256": sha256,
        "git_commit": git_commit,
        "requested_start_s": start,
        "requested_duration_s": duration,
        "decode_format": {
            "rate": SOURCE_RATE,
            "channels": 2,
            "encoding": "S16LE",
        },
        "ffmpeg_version": ffmpeg_version,
        "chunk_samples": chunk_samples,
        "analyser": {
            k: v for k, v in pipeline_info.items() if k != "violations"
        },
        "n_rows": n_rows,
        "analysed_duration_s": analysed_s,
        "violations": pipeline_info.get("violations", []),
    }
    meta_path.write_text(json.dumps(meta, indent=2))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def git_commit(repo_root: Path = _REPO_ROOT) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HueSync offline native analyser acceptance harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, metavar="FILE")
    parser.add_argument("--output", required=True, metavar="CSV")
    parser.add_argument(
        "--backend",
        default="v2",
        choices=sorted(ENGINES.keys()),
        metavar="BACKEND",
        help=f"spectrum engine: {sorted(ENGINES.keys())} (default: v2)",
    )
    parser.add_argument("--start", type=float, default=None, metavar="SECONDS")
    parser.add_argument("--duration", type=float, default=None, metavar="SECONDS")
    parser.add_argument("--ffmpeg", default=None, metavar="PATH")
    args = parser.parse_args(argv)

    ffmpeg = find_ffmpeg(args.ffmpeg)
    if ffmpeg is None:
        print(
            "ERROR: ffmpeg not found.\n"
            "  On Debian/Ubuntu: sudo apt install ffmpeg\n"
            "  On WSL:           supply --ffmpeg /path/to/ffmpeg.exe",
            file=sys.stderr,
        )
        return 1

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = output_path.with_name(output_path.name + ".meta.json")

    digest = sha256_file(input_path)
    commit = git_commit()
    ver = ffmpeg_version(ffmpeg)

    print(f"Input:    {input_path.name}")
    print(f"SHA-256:  {digest[:16]}…")
    print(f"Commit:   {commit}")
    print(f"Backend:  {args.backend}")
    print(f"ffmpeg:   {Path(ffmpeg).name} ({ver})")
    if args.start is not None:
        print(f"Start:    {args.start} s")
    if args.duration is not None:
        print(f"Duration: {args.duration} s")
    print()

    print("Decoding…")
    raw_pcm = decode_audio(ffmpeg, input_path, start=args.start, duration=args.duration)
    n_source_frames = len(raw_pcm) // 4
    print(f"  {n_source_frames:,} stereo frames  ({n_source_frames / SOURCE_RATE:.2f} s)")

    print("Analysing…")
    rows, info = analyse_pcm(raw_pcm, backend=args.backend)
    print(f"  {len(rows):,} snapshots")

    if not rows:
        print("WARNING: no snapshots produced — input may be shorter than STFT warmup (~50 ms)")

    violations = info.get("violations", [])
    if violations:
        print(f"\nHARD INVARIANT VIOLATIONS: {len(violations)}")
        for v in violations[:20]:
            print(f"  {v}")
        if len(violations) > 20:
            print(f"  … and {len(violations) - 20} more")

    write_csv(output_path, rows)
    write_meta(
        meta_path,
        input_name=input_path.name,
        sha256=digest,
        git_commit=commit,
        start=args.start,
        duration=args.duration,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=len(rows),
        ffmpeg_version=ver,
    )
    print(f"\nCSV:      {output_path}")
    print(f"Metadata: {meta_path}")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
