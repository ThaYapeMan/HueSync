#!/usr/bin/env python3
"""Diagnostic: compare cava bars vs PcmAudioPipeline bars side by side.

Usage (from the LXC):
    /opt/huesync/.venv/bin/python3 scripts/compare_bars.py --from-config
    /opt/huesync/.venv/bin/python3 scripts/compare_bars.py --mac AA:BB:CC:DD:EE:FF --bars 30
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# sys.path: add src/ so we can import huesync even when run as a script
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from huesync.pcm_source import WINDOW_SIZE, SqueezeliteShmSource  # noqa: E402
from huesync.sync_engine import BandNormaliser, FifoReader  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = os.environ.get("HUESYNC_CONFIG", "/etc/huesync/config.json")
WARMUP_SECONDS = 5  # exclude first N seconds from summary statistics


# ---------------------------------------------------------------------------
# Bar computation (mirrors PcmAudioPipeline._mag_to_bar_bytes)
# ---------------------------------------------------------------------------


def mag_to_bar_bytes(
    mag: np.ndarray,
    n_bars: int,
    lower_hz: float,
    upper_hz: float,
    sample_rate: int,
) -> bytes:
    """Average STFT magnitude bins into n_bars log-spaced bars → bytes 0-255."""
    n_bins = len(mag)
    log_lo = math.log10(max(lower_hz, 1.0))
    log_hi = math.log10(max(upper_hz, lower_hz + 1.0))
    result = bytearray(n_bars)
    for i in range(n_bars):
        f_lo = 10.0 ** (log_lo + i / n_bars * (log_hi - log_lo))
        f_hi = 10.0 ** (log_lo + (i + 1) / n_bars * (log_hi - log_lo))
        bin_lo = max(0, round(f_lo * WINDOW_SIZE / sample_rate))
        bin_hi = min(n_bins, max(bin_lo + 1, round(f_hi * WINDOW_SIZE / sample_rate)))
        val = float(np.mean(mag[bin_lo:bin_hi])) if bin_hi > bin_lo else 0.0
        result[i] = min(255, int(val))
    return bytes(result)


def compute_bin_coverage(
    n_bars: int, lower_hz: float, upper_hz: float, sample_rate: int
) -> list[int]:
    """Return the number of STFT bins covered by each bar (for DIAG output)."""
    log_lo = math.log10(max(lower_hz, 1.0))
    log_hi = math.log10(max(upper_hz, lower_hz + 1.0))
    n_bins = WINDOW_SIZE // 2 + 1
    coverage = []
    for i in range(n_bars):
        f_lo = 10.0 ** (log_lo + i / n_bars * (log_hi - log_lo))
        f_hi = 10.0 ** (log_lo + (i + 1) / n_bars * (log_hi - log_lo))
        bin_lo = max(0, round(f_lo * WINDOW_SIZE / sample_rate))
        bin_hi = min(n_bins, max(bin_lo + 1, round(f_hi * WINDOW_SIZE / sample_rate)))
        coverage.append(bin_hi - bin_lo)
    return coverage


# ---------------------------------------------------------------------------
# Band metrics (matching CavaPipeline slices)
# ---------------------------------------------------------------------------


def band_stats(bars_float: list[float], n_bars: int) -> tuple[float, float, float, float]:
    """Return (std, bass, mid, treble) for a list of float bars (0.0-1.0).

    Slices match CavaPipeline:
      bass   = [0 : 20 %] of bars
      mid    = [0 : 55 %] of bars  (cumulative — all below plus up to 55 %)
      treble = [0 : 100%] of bars  (full average)
    """
    arr = np.array(bars_float, dtype=np.float64)
    std = float(np.std(arr))

    def _avg(start_frac: float, end_frac: float) -> float:
        lo = int(start_frac * n_bars)
        hi = max(int(end_frac * n_bars), lo + 1)
        hi = min(hi, n_bars)
        seg = arr[lo:hi]
        return float(np.mean(seg)) if len(seg) else 0.0

    bass = _avg(0.0, 0.20)
    mid = _avg(0.0, 0.55)
    treble = _avg(0.0, 1.00)
    return std, bass, mid, treble


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Compare cava bars vs PcmAudioPipeline bars from the same squeezelite SHM source."
        )
    )
    p.add_argument("--mac", help="Squeezelite player MAC address (e.g. AA:BB:CC:DD:EE:FF)")
    p.add_argument("--bars", type=int, default=30, help="Number of bars (default: 30)")
    p.add_argument("--lower", type=int, default=50, help="Lower cutoff Hz (default: 50)")
    p.add_argument("--upper", type=int, default=12000, help="Upper cutoff Hz (default: 12000)")
    p.add_argument("--duration", type=int, default=120, help="Run duration seconds (default: 120)")
    p.add_argument(
        "--from-config",
        action="store_true",
        help="Read MAC/bars/cutoffs from the running HueSync config.json",
    )
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to config.json")
    return p.parse_args()


def read_config_params(config_path: str) -> dict:
    """Read active coupling's MAC, bars, lower_cutoff_freq, higher_cutoff_freq from config.json."""
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    active_id = data.get("active_coupling_id")
    if not active_id:
        sys.exit("ERROR: No active_coupling_id in config.json. Is a coupling active?")

    couplings = data.get("couplings", [])
    coupling = next((c for c in couplings if c["id"] == active_id), None)
    if coupling is None:
        sys.exit(f"ERROR: Active coupling {active_id!r} not found in config.json.")

    # Resolve player → MAC
    player_id = coupling.get("player_id")
    players = data.get("virtual_players", [])
    player = next((pl for pl in players if pl["id"] == player_id), None)
    if player is None:
        sys.exit(f"ERROR: VirtualPlayer {player_id!r} not found in config.json.")
    mac = player.get("player_mac")
    if not mac:
        sys.exit("ERROR: VirtualPlayer has no player_mac — has it been activated at least once?")

    # Resolve analyser → bars / cutoffs
    analyser_id = coupling.get("analyser_id")
    analysers = data.get("analysers", [])
    # Fall back to inline coupling fields if no separate analyser entity
    if analyser_id:
        analyser = next((a for a in analysers if a["id"] == analyser_id), None)
        if analyser is None:
            sys.exit(f"ERROR: Analyser {analyser_id!r} not found in config.json.")
        bars = analyser.get("bars", 30)
        lower = analyser.get("lower_cutoff_freq", 50)
        upper = analyser.get("higher_cutoff_freq", 12000)
    else:
        # Coupling may carry these directly
        bars = coupling.get("bars", 30)
        lower = coupling.get("lower_cutoff_freq", 50)
        upper = coupling.get("higher_cutoff_freq", 12000)

    print(
        f"[config] active_coupling={active_id}  mac={mac}"
        f"  bars={bars}  lower={lower}  upper={upper}"
    )
    return {"mac": mac, "bars": bars, "lower": lower, "upper": upper}


# ---------------------------------------------------------------------------
# Cava side: thread that polls FifoReader and normalises with BandNormaliser
# ---------------------------------------------------------------------------


class CavaSide:
    """Manages a cava subprocess + FifoReader and exposes latest normalised bars."""

    def __init__(self, mac: str, bars: int, lower: int, upper: int, tmpdir: str) -> None:
        self._mac = mac
        self._bars = bars
        self._lower = lower
        self._upper = upper
        self._tmpdir = tmpdir

        self._fifo_path = os.path.join(tmpdir, "compare.fifo")
        self._conf_path = os.path.join(tmpdir, "compare.conf")
        self._log_path = os.path.join(tmpdir, "compare.cava.log")

        self._cava_proc: subprocess.Popen | None = None
        self._fifo_reader: FifoReader | None = None
        self._normaliser = BandNormaliser()

        self._lock = threading.Lock()
        self._latest_raw: bytes | None = None    # most-recent raw cava frame
        self._latest_normed: list[float] | None = None  # most-recent normalised bars

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Create FIFO, write cava config, start FifoReader, then spawn cava."""
        # 1. Create FIFO
        try:
            os.mkfifo(self._fifo_path)
        except FileExistsError:
            pass

        # 2. Write cava config
        conf = (
            f"[general]\n"
            f"bars = {self._bars}\n"
            f"lower_cutoff_freq = {self._lower}\n"
            f"higher_cutoff_freq = {self._upper}\n"
            f"\n"
            f"[input]\n"
            f"method = shmem\n"
            f"source = /squeezelite-{self._mac}\n"
            f"\n"
            f"[output]\n"
            f"method = raw\n"
            f"raw_target = {self._fifo_path}\n"
            f"data_format = binary\n"
            f"bit_format = 8bit\n"
            f"channels = mono\n"
        )
        Path(self._conf_path).write_text(conf)

        # 3. Start FifoReader FIRST (opens read end so cava writer won't SIGPIPE)
        self._fifo_reader = FifoReader(self._fifo_path, frame_size=self._bars)
        self._fifo_reader.start()

        # 4. Spawn cava
        Path(self._log_path).write_text("")
        cava_log = open(self._log_path, "ab")  # noqa: SIM115
        self._cava_proc = subprocess.Popen(
            ["cava", "-p", self._conf_path],
            stdout=subprocess.DEVNULL,
            stderr=cava_log,
        )
        print(f"[cava] started pid={self._cava_proc.pid}  conf={self._conf_path}")

        # 5. Start polling thread
        self._thread = threading.Thread(target=self._run, daemon=True, name="cava-poll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._fifo_reader:
            self._fifo_reader.stop()
        if self._cava_proc:
            self._cava_proc.terminate()
            try:
                self._cava_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._cava_proc.kill()
            print(f"[cava] stopped (log: {self._log_path})")

    def _run(self) -> None:
        """Poll FifoReader at ~50 Hz, normalise, store latest."""
        while not self._stop.is_set():
            raw = self._fifo_reader.latest_frame() if self._fifo_reader else None
            if raw is not None:
                normed = self._normaliser.normalise(raw)
                bars_float = [v / 255.0 for v in normed]
                with self._lock:
                    self._latest_raw = raw
                    self._latest_normed = bars_float
            self._stop.wait(0.020)  # 50 Hz poll

    # -- Read ----------------------------------------------------------------

    def latest(self) -> tuple[bytes | None, list[float] | None]:
        """Return (raw_frame, normalised_bars_float) or (None, None)."""
        with self._lock:
            return self._latest_raw, self._latest_normed


# ---------------------------------------------------------------------------
# PCM side: thread that reads SHM, runs STFT, normalises
# ---------------------------------------------------------------------------


class PcmSide:
    """Reads squeezelite SHM, runs STFT and mag_to_bar_bytes, normalises."""

    def __init__(self, mac: str, bars: int, lower: int, upper: int) -> None:
        self._mac = mac
        self._bars = bars
        self._lower = float(lower)
        self._upper = float(upper)

        self._shm = SqueezeliteShmSource()
        self._normaliser = BandNormaliser()

        self._lock = threading.Lock()
        self._latest_raw_bytes: bytes | None = None   # raw bar bytes before normalisation
        self._latest_normed: list[float] | None = None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_rate: int = 44100

        # PcmStft is imported lazily after we know the sample rate
        self._stft = None

    def start(self) -> None:
        """Open SHM, initialise STFT, start polling thread."""
        self._shm.open(self._mac)
        self._sample_rate = self._shm.sample_rate
        print(f"[pcm]  SHM opened  mac={self._mac}  sample_rate={self._sample_rate}")

        from huesync.pcm_source import PcmStft  # local import: sample_rate known now
        self._stft = PcmStft(self._sample_rate)

        self._thread = threading.Thread(target=self._run, daemon=True, name="pcm-poll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._shm.close()
        print("[pcm]  SHM closed")

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def _run(self) -> None:
        """Poll SHM at ~100 Hz, push through STFT, normalise, store latest."""
        while not self._stop.is_set():
            samples = self._shm.read_new()
            if len(samples) > 0 and self._stft is not None:
                frames = self._stft.push(samples)
                if frames:
                    # Use the most recent STFT frame
                    mag = frames[-1]
                    raw_bytes = mag_to_bar_bytes(
                        mag, self._bars, self._lower, self._upper, self._sample_rate
                    )
                    normed = self._normaliser.normalise(raw_bytes)
                    bars_float = [v / 255.0 for v in normed]
                    with self._lock:
                        self._latest_raw_bytes = raw_bytes
                        self._latest_normed = bars_float
            self._stop.wait(0.010)  # 100 Hz poll

    def latest(self) -> tuple[bytes | None, list[float] | None]:
        """Return (raw_bar_bytes, normalised_bars_float) or (None, None)."""
        with self._lock:
            return self._latest_raw_bytes, self._latest_normed


# ---------------------------------------------------------------------------
# Summary statistics accumulator
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self) -> None:
        self.std_cava: list[float] = []
        self.std_pcm: list[float] = []
        self.ratios: list[float] = []
        # Per-second correlation (dot product normalised)
        self.correlations: list[float] = []

    def record(
        self,
        std_c: float,
        std_p: float,
        bars_c: list[float],
        bars_p: list[float],
    ) -> None:
        self.std_cava.append(std_c)
        self.std_pcm.append(std_p)
        self.ratios.append(std_c / std_p if std_p > 1e-9 else float("nan"))
        # Pearson-like: cosine similarity between bar vectors
        arr_c = np.array(bars_c)
        arr_p = np.array(bars_p)
        denom = (np.linalg.norm(arr_c) * np.linalg.norm(arr_p))
        corr = float(np.dot(arr_c, arr_p) / denom) if denom > 1e-9 else 0.0
        self.correlations.append(corr)

    def print_summary(self) -> None:
        if not self.std_cava:
            print("\n[summary] No data collected after warmup.")
            return

        valid_ratios = [r for r in self.ratios if not math.isnan(r)]

        print("\n" + "=" * 72)
        print("[SUMMARY]")
        print(f"  samples (seconds): {len(self.std_cava)}")
        print(f"  mean std_cava : {np.mean(self.std_cava):.4f}")
        print(f"  mean std_pcm  : {np.mean(self.std_pcm):.4f}")
        if valid_ratios:
            print(f"  mean ratio    : {np.mean(valid_ratios):.3f}")
        else:
            print("  mean ratio    : n/a")
        print(f"  mean corr     : {np.mean(self.correlations):.4f}  (1.0 = perfectly aligned)")

        if valid_ratios:
            print("\n  Lag / sync analysis (cava vs pcm state each second):")
            for i, (sc, sp, corr) in enumerate(
                zip(self.std_cava, self.std_pcm, self.correlations, strict=False)
            ):
                both_loud = sc > 0.05 and sp > 0.05
                both_quiet = sc < 0.02 and sp < 0.02
                state = "BOTH_LOUD" if both_loud else ("BOTH_QUIET" if both_quiet else "DIVERGED")
                print(f"    t+{i+WARMUP_SECONDS+1:3d}s  cava_std={sc:.3f}  pcm_std={sp:.3f}  "
                      f"corr={corr:.3f}  [{state}]")
        print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    # Resolve parameters
    if args.from_config:
        params = read_config_params(args.config)
        mac = params["mac"]
        bars = params["bars"]
        lower = params["lower"]
        upper = params["upper"]
    else:
        if not args.mac:
            sys.exit("ERROR: --mac is required unless --from-config is used.")
        mac = args.mac
        bars = args.bars
        lower = args.lower
        upper = args.upper

    duration = args.duration

    print(
        f"compare_bars.py  mac={mac}  bars={bars}"
        f"  lower={lower}  upper={upper}  duration={duration}s"
    )
    print(f"Warmup: first {WARMUP_SECONDS}s excluded from summary statistics.\n")

    tmpdir = tempfile.mkdtemp(prefix="compare_bars_")
    print(f"[tmp]  working directory: {tmpdir}")

    cava_side = CavaSide(mac, bars, lower, upper, tmpdir)
    pcm_side = PcmSide(mac, bars, lower, upper)

    try:
        # Start PCM side first (no ordering requirement, but SHM should be ready)
        pcm_side.start()
        cava_side.start()

        stats = Stats()
        t0 = time.monotonic()
        tick = 0
        diag_interval = 10  # seconds between DIAG lines

        # Pre-compute bin coverage (static, used in DIAG lines)
        sample_rate = pcm_side.sample_rate
        bin_coverage = compute_bin_coverage(bars, float(lower), float(upper), sample_rate)

        print("\n[ready] both sources running — collecting data...\n")

        while True:
            elapsed = time.monotonic() - t0
            if elapsed >= duration:
                break

            tick += 1
            is_warmup = elapsed < WARMUP_SECONDS

            # Snapshot latest from both sides
            raw_cava, normed_cava = cava_side.latest()
            raw_pcm, normed_pcm = pcm_side.latest()

            if is_warmup:
                print(f"  [warming up... {elapsed:.0f}s / {WARMUP_SECONDS}s]")
                time.sleep(1.0)
                continue

            t_label = f"T+{elapsed:5.1f}"

            # --- Cava line ---
            if normed_cava is not None and raw_cava is not None:
                std_c, bass_c, mid_c, treble_c = band_stats(normed_cava, bars)
                raw_preview = " ".join(f"{v:3d}" for v in raw_cava[:15])
                print(
                    f"[{t_label}]  cava  bars=[{raw_preview}...]"
                    f"  std={std_c:.3f}  bass={bass_c:.2f}  mid={mid_c:.2f}  treble={treble_c:.2f}"
                )
            else:
                std_c = 0.0
                bass_c = mid_c = treble_c = 0.0
                normed_cava = [0.0] * bars
                print(f"[{t_label}]  cava  (no data yet)")

            # --- PCM line ---
            if normed_pcm is not None and raw_pcm is not None:
                std_p, bass_p, mid_p, treble_p = band_stats(normed_pcm, bars)
                raw_preview = " ".join(f"{v:3d}" for v in raw_pcm[:15])
                pcm_bar_max = max(raw_pcm) if raw_pcm else 0
                print(
                    f"[{t_label}]  pcm   bars=[{raw_preview}...]"
                    f"  std={std_p:.3f}  bass={bass_p:.2f}  mid={mid_p:.2f}  treble={treble_p:.2f}"
                )
            else:
                std_p = 0.0
                bass_p = mid_p = treble_p = 0.0
                normed_pcm = [0.0] * bars
                pcm_bar_max = 0
                print(f"[{t_label}]  pcm   (no data yet)")

            # --- RATIO line ---
            ratio_std = std_c / std_p if std_p > 1e-9 else float("nan")
            ratio_bass = bass_c / bass_p if bass_p > 1e-9 else float("nan")
            ratio_mid = mid_c / mid_p if mid_p > 1e-9 else float("nan")
            ratio_treble = treble_c / treble_p if treble_p > 1e-9 else float("nan")

            def _fmt(v: float) -> str:
                return f"{v:.2f}" if not math.isnan(v) else " n/a"

            print(
                f"[{t_label}]  RATIO"
                f"  std_cava/std_pcm={_fmt(ratio_std)}"
                f"  bass_ratio={_fmt(ratio_bass)}"
                f"  mid_ratio={_fmt(ratio_mid)}"
                f"  treble_ratio={_fmt(ratio_treble)}"
            )

            # Record to stats
            stats.record(std_c, std_p, normed_cava, normed_pcm)

            # --- DIAG line every diag_interval seconds ---
            # Use tick as proxy: tick increments by 1 per second; first diag at tick==diag_interval
            if tick % diag_interval == 0:
                # Hypothesis 2: bin coverage per bar (first, middle, last)
                mid_idx = bars // 2
                last_idx = bars - 1
                cov_parts = (
                    f"bar0={bin_coverage[0]}bins"
                    f"  bar{mid_idx}={bin_coverage[mid_idx]}bins"
                    f"  bar{last_idx}={bin_coverage[last_idx]}bins"
                )
                # Scale issue: PCM bar max vs cava range
                pcm_pct = f"{pcm_bar_max / 255 * 100:.0f}%"
                diag_t = f"T+{elapsed:3.0f}"
                print(
                    f"[DIAG {diag_t}]"
                    f"  bin_coverage: {cov_parts}"
                    f"  (high-freq bars average more bins -> dilution risk)"
                )
                print(
                    f"[DIAG {diag_t}]"
                    f"  pcm_raw_scale: bar_max={pcm_bar_max}"
                    f"  (PCM bars top out at {pcm_bar_max}/255 = {pcm_pct} of cava range)"
                )

            print()
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[interrupted by user]")
    finally:
        print("\n[shutdown] stopping sources...")
        cava_side.stop()
        pcm_side.stop()

    stats.print_summary()


if __name__ == "__main__":
    main()
