"""Tests for scripts/run_acceptance.py.

Validates CSV schema, hard correctness invariants, timestamp derivation,
determinism, and metadata content.  All tests use synthetic audio so they
never require copyrighted music files.

ffmpeg-dependent tests are skipped when no usable ffmpeg is found.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import sys
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

# Make the scripts/ directory importable.
_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from run_acceptance import (  # noqa: E402
    CANONICAL_RATE,
    CHUNK_SAMPLES,
    CSV_COLUMNS,
    HOP,
    SOURCE_RATE,
    analyse_pcm,
    find_ffmpeg,
    git_commit,
    sha256_file,
    write_csv,
    write_meta,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ffmpeg() -> str | None:
    return find_ffmpeg()


@pytest.fixture(scope="session")
def _tmp_accept_dir() -> Iterator[Path]:
    """Session-scoped temp dir under the repo root, accessible to Windows ffmpeg.

    Linux /tmp/ paths are invisible to Windows processes.  This directory sits
    under the WSL mount root so wslpath can translate it for Windows ffmpeg.exe.
    Cleaned up at the end of the test session; .gitignore prevents stale files
    from appearing in git status if a session crashes.
    """
    d = Path(__file__).parent / "tmp_accept"
    d.mkdir(exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sine_wav(_tmp_accept_dir: Path) -> Iterator[Path]:
    """2-second 440 Hz sine wave, 44100 Hz stereo S16LE WAV."""
    path = _tmp_accept_dir / "sine_440_harness_test.wav"
    rate = SOURCE_RATE
    freq = 440
    n = int(rate * 2.0)
    t = np.arange(n, dtype=np.float32) / rate
    sig = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype(np.int16)
    stereo = np.column_stack([sig, sig])
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(stereo.tobytes())
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def sine_raw_pcm() -> bytes:
    """3-second 440 Hz sine, returned as raw S16LE 44100 Hz stereo bytes."""
    rate = SOURCE_RATE
    freq = 440
    n = int(rate * 3.0)
    t = np.arange(n, dtype=np.float32) / rate
    sig = (np.sin(2 * np.pi * freq * t) * 0.5 * 32767).astype(np.int16)
    stereo = np.column_stack([sig, sig])
    return stereo.tobytes()


@pytest.fixture
def silence_raw_pcm() -> bytes:
    """1-second digital silence, raw S16LE 44100 Hz stereo."""
    return bytes(SOURCE_RATE * 4)


@pytest.fixture
def rows_from_sine(sine_raw_pcm: bytes) -> list[dict]:
    rows, _ = analyse_pcm(sine_raw_pcm)
    return rows


@pytest.fixture
def info_from_sine(sine_raw_pcm: bytes) -> dict:
    _, info = analyse_pcm(sine_raw_pcm)
    return info


# ---------------------------------------------------------------------------
# 1. CSV schema
# ---------------------------------------------------------------------------


def test_csv_columns_exact(rows_from_sine: list[dict]) -> None:
    assert rows_from_sine, "Sine signal must produce at least one snapshot"
    assert list(rows_from_sine[0].keys()) == CSV_COLUMNS


def test_csv_columns_count() -> None:
    # t_s(1) + backend(1) + effective_backend(1) + effective_engine(1)
    # + bars(30) + scalars(13) + conditioning(3)
    # + audit(sequence, epoch, sample_start, sample_end = 4) = 54
    assert len(CSV_COLUMNS) == 54


def test_csv_bar_columns_named_correctly() -> None:
    bar_cols = [c for c in CSV_COLUMNS if c.startswith("bar_")]
    assert len(bar_cols) == 30
    assert bar_cols[0] == "bar_00"
    assert bar_cols[-1] == "bar_29"


def test_csv_roundtrip(tmp_path: Path, rows_from_sine: list[dict]) -> None:
    out = tmp_path / "roundtrip.csv"
    write_csv(out, rows_from_sine)
    with out.open() as f:
        reader = csv.DictReader(f)
        loaded = list(reader)
    assert len(loaded) == len(rows_from_sine)
    assert list(loaded[0].keys()) == CSV_COLUMNS


# ---------------------------------------------------------------------------
# 2. Finite values for all numeric fields
# ---------------------------------------------------------------------------


def test_all_bar_values_finite(rows_from_sine: list[dict]) -> None:
    for row in rows_from_sine:
        for i in range(30):
            v = float(row[f"bar_{i:02d}"])
            assert math.isfinite(v), f"bar_{i:02d} non-finite at t={row['t_s']}: {v}"


def test_all_scalar_fields_finite(rows_from_sine: list[dict]) -> None:
    scalar_fields = [
        "bass", "mid", "full", "centroid",
        "onset_strength",
        "onset_bass_strength", "onset_mid_strength", "onset_treble_strength",
        "relative_exertion", "peak_ema", "bars_smooth_mean", "bars_smooth_max",
    ]
    for row in rows_from_sine:
        for field in scalar_fields:
            v = float(row[field])
            assert math.isfinite(v), f"{field} non-finite at t={row['t_s']}: {v}"


# ---------------------------------------------------------------------------
# 3. Exactly 30 bars per row
# ---------------------------------------------------------------------------


def test_exactly_30_bars_per_row(rows_from_sine: list[dict]) -> None:
    for row in rows_from_sine:
        bar_count = sum(1 for k in row if k.startswith("bar_"))
        assert bar_count == 30, f"Expected 30 bars, got {bar_count} at t={row['t_s']}"


# ---------------------------------------------------------------------------
# 4. Deterministic output from identical input
# ---------------------------------------------------------------------------


def test_deterministic_same_raw_pcm(sine_raw_pcm: bytes) -> None:
    rows_a, _ = analyse_pcm(sine_raw_pcm)
    rows_b, _ = analyse_pcm(sine_raw_pcm)
    assert len(rows_a) == len(rows_b), "Row count differs between runs"
    # The "epoch" column is a random UUID emitted by AudioCanonicalizer and
    # is intentionally non-deterministic; every other column must match.
    _non_deterministic = {"epoch"}
    for i, (ra, rb) in enumerate(zip(rows_a, rows_b, strict=True)):
        for col in CSV_COLUMNS:
            if col in _non_deterministic:
                continue
            assert ra[col] == rb[col], (
                f"Column '{col}' differs at row {i}: {ra[col]} vs {rb[col]}"
            )


def test_deterministic_different_objects(sine_raw_pcm: bytes) -> None:
    # Ensure the PCM bytes object is not shared (separate memory views).
    pcm_copy = bytes(sine_raw_pcm)
    rows_a, _ = analyse_pcm(sine_raw_pcm)
    rows_b, _ = analyse_pcm(pcm_copy)
    assert len(rows_a) == len(rows_b)
    assert all(
        ra["t_s"] == rb["t_s"] and ra["full"] == rb["full"]
        for ra, rb in zip(rows_a, rows_b, strict=True)
    )


# ---------------------------------------------------------------------------
# 5. Metadata: SHA-256 and git commit
# ---------------------------------------------------------------------------


def test_write_meta_contains_sha256(tmp_path: Path, sine_wav: Path, sine_raw_pcm: bytes) -> None:
    _, info = analyse_pcm(sine_raw_pcm)
    meta_path = tmp_path / "out.csv.meta.json"
    write_meta(
        meta_path,
        input_name=sine_wav.name,
        sha256=sha256_file(sine_wav),
        git_commit=git_commit(),
        start=None,
        duration=None,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=42,
    )
    meta = json.loads(meta_path.read_text())
    assert "sha256" in meta
    assert len(meta["sha256"]) == 64  # hex SHA-256
    # SHA-256 must be a valid hex string
    int(meta["sha256"], 16)


def test_write_meta_contains_git_commit(
    tmp_path: Path, sine_wav: Path, sine_raw_pcm: bytes
) -> None:
    _, info = analyse_pcm(sine_raw_pcm)
    meta_path = tmp_path / "out.csv.meta.json"
    write_meta(
        meta_path,
        input_name=sine_wav.name,
        sha256="0" * 64,
        git_commit="abc1234",
        start=None,
        duration=None,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=10,
    )
    meta = json.loads(meta_path.read_text())
    assert meta["git_commit"] == "abc1234"


def test_write_meta_fields_complete(tmp_path: Path, sine_wav: Path, sine_raw_pcm: bytes) -> None:
    _, info = analyse_pcm(sine_raw_pcm)
    meta_path = tmp_path / "out.csv.meta.json"
    write_meta(
        meta_path,
        input_name="test.wav",
        sha256="0" * 64,
        git_commit="abcdef0",
        start=5.0,
        duration=30.0,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=50,
        ffmpeg_version="6.1.1",
    )
    meta = json.loads(meta_path.read_text())
    required = {
        "source", "sha256", "git_commit",
        "requested_start_s", "requested_duration_s",
        "decode_format", "chunk_samples", "ffmpeg_version",
        "analyser", "n_rows", "analysed_duration_s",
    }
    assert required.issubset(meta.keys())
    assert meta["source"] == "test.wav"
    assert meta["requested_start_s"] == 5.0
    assert meta["requested_duration_s"] == 30.0
    assert meta["n_rows"] == 50
    assert meta["chunk_samples"] == CHUNK_SAMPLES
    assert meta["ffmpeg_version"] == "6.1.1"
    analyser = meta["analyser"]
    assert analyser["fft_size"] == 2048
    assert analyser["hop"] == 480
    assert analyser["window"] == "hamming"
    assert analyser["canonical_rate"] == CANONICAL_RATE
    assert analyser["n_bars"] == 30


def test_write_meta_analysed_duration(tmp_path: Path, sine_wav: Path, sine_raw_pcm: bytes) -> None:
    rows, info = analyse_pcm(sine_raw_pcm)
    meta_path = tmp_path / "out.csv.meta.json"
    write_meta(
        meta_path,
        input_name="x.wav",
        sha256="0" * 64,
        git_commit="abc",
        start=None,
        duration=None,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=len(rows),
    )
    meta = json.loads(meta_path.read_text())
    expected_s = len(rows) * info["hop"] / info["canonical_rate"]
    assert abs(meta["analysed_duration_s"] - expected_s) < 0.01


# ---------------------------------------------------------------------------
# 6. Timestamps: monotonic, sample-derived, one-hop cadence
# ---------------------------------------------------------------------------


def test_timestamps_monotonically_increasing(rows_from_sine: list[dict]) -> None:
    timestamps = [float(r["t_s"]) for r in rows_from_sine]
    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"Timestamp not monotonic at index {i}: {timestamps[i]} < {timestamps[i-1]}"
        )


def test_timestamps_positive(rows_from_sine: list[dict]) -> None:
    for row in rows_from_sine:
        assert float(row["t_s"]) >= 0.0


def test_timestamps_sample_derived_spacing(rows_from_sine: list[dict]) -> None:
    # After canonical re-chunking, each row corresponds to exactly one STFT hop
    # (HOP=480 canonical samples = 10 ms at 48 kHz).  What proves sample-derivation
    # is the extremely low variance: wall-clock spacing has high jitter;
    # sample-derived spacing has coefficient-of-variation < 1 %.
    timestamps = [float(r["t_s"]) for r in rows_from_sine]
    diffs = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
    if len(diffs) < 5:
        pytest.skip("Too few rows to check spacing")
    mean_diff = sum(diffs) / len(diffs)
    hop_s = HOP / CANONICAL_RATE  # 480/48000 = 10 ms
    # Spacing must be >= 1 hop and <= 5 hops (5 hops = 50 ms covers any soxr batch size).
    assert hop_s * 0.9 <= mean_diff <= hop_s * 5, (
        f"Mean spacing {mean_diff*1000:.2f} ms outside expected range "
        f"[{hop_s*0.9*1000:.0f} ms, {hop_s*5*1000:.0f} ms]"
    )
    # Coefficient of variation must be < 2 % (sample-derived → deterministic, low jitter).
    variance = sum((d - mean_diff) ** 2 for d in diffs) / len(diffs)
    std = variance ** 0.5
    cv = std / mean_diff if mean_diff > 0 else float("inf")
    assert cv < 0.02, (
        f"Timestamp spacing CV={cv:.3f} too high — may be wall-clock-derived, "
        f"not sample-derived (expected < 2 %)"
    )


def test_timestamps_one_hop_cadence(rows_from_sine: list[dict]) -> None:
    """After re-chunking, mean row spacing must be close to exactly one STFT hop."""
    timestamps = [float(r["t_s"]) for r in rows_from_sine]
    diffs = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
    if len(diffs) < 10:
        pytest.skip("Too few rows to check cadence")
    mean_diff = sum(diffs) / len(diffs)
    hop_s = HOP / CANONICAL_RATE  # 10 ms
    assert abs(mean_diff - hop_s) / hop_s < 0.05, (
        f"Mean row spacing {mean_diff*1000:.3f} ms should be ~{hop_s*1000:.1f} ms (one hop); "
        f"deviation {abs(mean_diff - hop_s)/hop_s*100:.1f}% > 5%"
    )


# ---------------------------------------------------------------------------
# 7. Hard invariants: bars in [0, 1]
# ---------------------------------------------------------------------------


def test_bars_in_unit_interval(rows_from_sine: list[dict]) -> None:
    for row in rows_from_sine:
        for i in range(30):
            v = float(row[f"bar_{i:02d}"])
            assert v >= -1e-6, f"bar_{i:02d} negative at t={row['t_s']}: {v}"
            assert v <= 1.0 + 1e-6, f"bar_{i:02d} > 1 at t={row['t_s']}: {v}"


def test_peak_ema_positive_after_warmup(rows_from_sine: list[dict]) -> None:
    for row in rows_from_sine:
        v = float(row["peak_ema"])
        assert v >= 0.0, f"peak_ema negative at t={row['t_s']}: {v}"


# ---------------------------------------------------------------------------
# 8. Silence → near-zero bars (correctness, not perceptual threshold)
# ---------------------------------------------------------------------------


def test_silence_produces_zero_bars(silence_raw_pcm: bytes) -> None:
    rows, _ = analyse_pcm(silence_raw_pcm)
    # Silence may not produce rows (short signal + warmup), OR if it does,
    # all bars must be near zero.
    for row in rows:
        for i in range(30):
            v = float(row[f"bar_{i:02d}"])
            assert v < 1e-6, (
                f"Silence must give near-zero bars; bar_{i:02d}={v:.2e} at t={row['t_s']}"
            )


# ---------------------------------------------------------------------------
# 9. Violations field: no violations on clean sine signal
# ---------------------------------------------------------------------------


def test_no_hard_violations_on_sine(info_from_sine: dict) -> None:
    assert info_from_sine["violations"] == [], (
        f"Expected no hard invariant violations; got: {info_from_sine['violations'][:5]}"
    )


# ---------------------------------------------------------------------------
# 10. Coverage: 3-second input must produce approximately one row per STFT hop
# ---------------------------------------------------------------------------


def test_row_count_3s_signal(sine_raw_pcm: bytes) -> None:
    """3-second signal must produce approximately 296 STFT rows after re-chunking.

    Without re-chunking: ~160 rows (≈1.6 s analysed, one row per ~893 canonical samples).
    With re-chunking:    ~296 rows (≈3.0 s analysed, one row per 480 canonical samples).

    Expected: source 3×44100=132300 frames → ~144000 canonical samples →
              (144000-2048)/480 ≈ 296 STFT frames.
    """
    rows, _ = analyse_pcm(sine_raw_pcm)
    assert len(rows) >= 250, (
        f"Expected ≥250 rows for 3 s signal (re-chunking fix); got {len(rows)} — "
        f"row count ≈160 indicates the re-chunking fix was not applied"
    )
    assert len(rows) <= 330, (
        f"Expected ≤330 rows for 3 s signal; got {len(rows)}"
    )


# ---------------------------------------------------------------------------
# 11. ffmpeg-dependent: full round-trip through WAV file
# ---------------------------------------------------------------------------


def test_ffmpeg_decode_roundtrip(ffmpeg: str | None, sine_wav: Path) -> None:
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    from run_acceptance import decode_audio

    raw = decode_audio(ffmpeg, sine_wav)
    # 2 s × 44100 Hz × 2 ch × 2 bytes = 352800 bytes
    expected_bytes = int(SOURCE_RATE * 2.0) * 4
    assert abs(len(raw) - expected_bytes) <= 8, (
        f"Decoded PCM length {len(raw)} far from expected {expected_bytes}"
    )


def test_ffmpeg_full_pipeline(ffmpeg: str | None, sine_wav: Path, tmp_path: Path) -> None:
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    from run_acceptance import decode_audio

    raw = decode_audio(ffmpeg, sine_wav)
    rows, info = analyse_pcm(raw)

    # 2s signal: ~196 rows after re-chunking
    assert len(rows) > 150, f"Expected >150 snapshots for 2s signal, got {len(rows)}"
    assert info["violations"] == []

    # Verify CSV columns present
    assert list(rows[0].keys()) == CSV_COLUMNS

    # Write and reload to exercise full output path (tmp_path: no Windows access needed)
    out_csv = tmp_path / "test_full_pipeline.csv"
    out_meta = tmp_path / "test_full_pipeline.csv.meta.json"
    write_csv(out_csv, rows)
    write_meta(
        out_meta,
        input_name=sine_wav.name,
        sha256=sha256_file(sine_wav),
        git_commit=git_commit(),
        start=None,
        duration=None,
        chunk_samples=CHUNK_SAMPLES,
        pipeline_info=info,
        n_rows=len(rows),
    )
    meta = json.loads(out_meta.read_text())
    assert len(meta["sha256"]) == 64
    assert meta["git_commit"] != ""


def test_ffmpeg_deterministic_via_file(ffmpeg: str | None, sine_wav: Path) -> None:
    """Two identical decodes of the same WAV produce identical analyse_pcm output."""
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    from run_acceptance import decode_audio

    raw_a = decode_audio(ffmpeg, sine_wav)
    raw_b = decode_audio(ffmpeg, sine_wav)
    assert raw_a == raw_b, "ffmpeg decode of same file must be byte-identical"

    rows_a, _ = analyse_pcm(raw_a)
    rows_b, _ = analyse_pcm(raw_b)
    # The "epoch" column is a random UUID from AudioCanonicalizer; strip it
    # before comparing to keep the rest of the row deterministic assertion.
    def _strip_epoch(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "epoch"} for r in rows]
    assert _strip_epoch(rows_a) == _strip_epoch(rows_b), (
        "analyse_pcm must produce identical output for identical input"
    )


def test_ffmpeg_start_duration(ffmpeg: str | None, sine_wav: Path) -> None:
    """--start / --duration limit the decoded window."""
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    from run_acceptance import decode_audio

    # Full 2s decode
    full = decode_audio(ffmpeg, sine_wav)
    # 1s with 0.5s start offset
    partial = decode_audio(ffmpeg, sine_wav, start=0.5, duration=1.0)

    expected_partial = int(SOURCE_RATE * 1.0) * 4
    assert abs(len(partial) - expected_partial) <= SOURCE_RATE // 10, (
        f"Partial decode length {len(partial)} unexpected for 1s clip"
    )
    assert len(partial) < len(full)
