"""Direct PCM tap on squeezelite's visualiser shared memory.

Reads the same segment that cava uses, but independently.  cava continues
driving colour; this is a parallel reader for STFT-based onset detection.

The lock at the start of vis_t is deliberately NOT taken.  Taking a read lock
can cause squeezelite to skip exporting blocks entirely when its trywrlock
fails — so politely locking would introduce the gaps we are trying to avoid.
A torn read means a handful of samples from the wrong position in a 2048-sample
FFT window: inaudible, and rare.
"""

from __future__ import annotations

import errno
import logging
import mmap
import os
import struct
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.lib.stride_tricks

from huesync.canonicalizer import (
    DataResult,
    DecodedSourceFrame,
    EndOfStream,
    InvalidationCause,
    SourceReadResult,
    StreamInvalidated,
    TemporarilyNoData,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PcmSource — source-agnostic PCM interface
# ---------------------------------------------------------------------------


@runtime_checkable
class PcmSource(Protocol):
    """Source-agnostic interface for raw PCM audio streams.

    Any audio source (squeezelite SHM, AirPlay named pipe, Roon, etc.)
    implements this interface. PcmAudioPipeline (sync_engine.py) operates
    exclusively on PcmSource and never on source-type-specific details.

    Adding a new VirtualPlayer type requires only a new PcmSource adapter;
    PcmAudioPipeline itself never changes. See CLAUDE.md for the full
    extension pattern.
    """

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read_new(self) -> np.ndarray:
        """Return newly available mono float32 samples in [−1.0, 1.0].

        Returns an empty array when no new samples are available.
        """
        ...

    @property
    def sample_rate(self) -> int: ...

    @property
    def running(self) -> bool:
        """True when the source is actively delivering audio data."""
        ...

VIS_BUF_SIZE = 16384  # s16 samples in the circular buffer (8192 stereo frames)
WINDOW_SIZE = 2048  # FFT window length in samples

# vis_t layout, glibc x86-64 (pthread_rwlock_t = 56 bytes):
#   offset  0  pthread_rwlock_t  56 B  (skipped — not taken)
#   offset 56  buf_size           4 B
#   offset 60  buf_index          4 B
#   offset 64  running            1 B  (+3 B padding)
#   offset 68  rate               4 B
#   offset 72  updated            8 B  (time_t)
#   offset 80  buffer[16384]  32768 B  (interleaved stereo s16)
#                             ------
#                             32848 B total
_HDR_OFFSET = 56
_HDR_FMT = "<IIBxxxIq"  # buf_size, buf_index, running, rate, updated
_HDR_SIZE = struct.calcsize(_HDR_FMT)  # 24
_BUF_OFFSET = _HDR_OFFSET + _HDR_SIZE  # 80
_MMAP_SIZE = _BUF_OFFSET + VIS_BUF_SIZE * 2  # 32848


class SqueezeliteShmSource:
    """Reads raw PCM from squeezelite's visualiser shared memory.

    Usage::

        src = SqueezeliteShmSource()
        src.open(mac)                 # call once after squeezelite starts
        samples = src.read_new()      # call at ~100 Hz; returns mono float32
        src.close()
    """

    def __init__(self) -> None:
        self._mm: mmap.mmap | None = None
        self._prev_index: int = 0

    def open(self, mac: str, *, _path: Path | None = None) -> None:
        """Map /dev/shm/squeezelite-{mac} into memory.

        ``_path`` is an internal override used by tests; production code
        always passes a MAC address and lets this method derive the path.
        """
        path = _path if _path is not None else Path(f"/dev/shm/squeezelite-{mac}")
        fd = path.open("rb")
        try:
            self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE, access=mmap.ACCESS_READ)
        finally:
            fd.close()
        # Snapshot the current write position so the first read_new() returns
        # only samples written *after* open(), not the entire history buffer.
        self._prev_index = self._read_header()[1]

    def close(self) -> None:
        """Unmap the shared memory segment."""
        if self._mm is not None:
            self._mm.close()
            self._mm = None

    def _read_header(self) -> tuple[int, int, bool, int, int]:
        """Return (buf_size, buf_index, running, rate, updated)."""
        if self._mm is None:
            raise RuntimeError("call open() before reading")
        self._mm.seek(_HDR_OFFSET)
        raw = self._mm.read(_HDR_SIZE)
        buf_size, buf_index, running_byte, rate, updated = struct.unpack(_HDR_FMT, raw)
        return buf_size, buf_index, bool(running_byte), rate, updated

    @property
    def sample_rate(self) -> int:
        """Sample rate in Hz, as reported by squeezelite."""
        return self._read_header()[3]

    @property
    def running(self) -> bool:
        """True when squeezelite is actively writing audio data."""
        return self._read_header()[2]

    def read_new(self) -> np.ndarray:
        """Return mono float32 samples written since the last call.

        Handles circular-buffer wraparound.  If more than half the buffer
        was written since the last read (we fell behind the writer), logs a
        warning and returns the newest ``VIS_BUF_SIZE // 2`` samples instead
        of attempting to reconstruct the full overwritten history.

        Returns an empty array when no new samples are available.
        """
        _, buf_index, _, _, _ = self._read_header()

        n_new = (buf_index - self._prev_index) % VIS_BUF_SIZE
        self._prev_index = buf_index  # always advance, even if we return early

        if n_new == 0:
            return np.empty(0, dtype=np.float32)

        if n_new > VIS_BUF_SIZE // 2:
            _log.warning(
                "PCM tap fell behind: %d new samples since last read (buffer %d). "
                "Returning newest window.",
                n_new,
                VIS_BUF_SIZE,
            )
            n_new = VIS_BUF_SIZE // 2

        n_new -= n_new % 2  # round down to complete stereo frames
        if n_new == 0:
            return np.empty(0, dtype=np.float32)

        start = (buf_index - n_new) % VIS_BUF_SIZE

        assert self._mm is not None
        if start + n_new <= VIS_BUF_SIZE:
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw = self._mm.read(n_new * 2)
        else:
            # Wraparound: two reads to reconstruct the contiguous window.
            tail = VIS_BUF_SIZE - start
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(_BUF_OFFSET)
            raw_head = self._mm.read((n_new - tail) * 2)
            raw = raw_tail + raw_head

        # Seqlock-style consistency check: if the writer advanced buf_index
        # while we were copying, the window may contain partially overwritten
        # samples.  Discard and let the next poll start fresh from buf_index.
        # This never takes the pthread_rwlock, so it cannot cause squeezelite
        # to skip exporting blocks.
        _, buf_index_after, _, _, _ = self._read_header()
        if buf_index_after != buf_index:
            _log.debug(
                "PCM tap: torn read detected (buf_index %d → %d), discarding block.",
                buf_index,
                buf_index_after,
            )
            return np.empty(0, dtype=np.float32)

        samples = np.frombuffer(raw, dtype=np.int16)
        # Interleaved stereo s16 → mono float32 in [−1.0, 1.0].
        # samples[0::2] = L channel, samples[1::2] = R channel.
        return (samples[0::2].astype(np.float32) + samples[1::2].astype(np.float32)) / (
            2.0 * 32768.0
        )


# ---------------------------------------------------------------------------
# HPSS parameters (Fitzgerald 2010)
# ---------------------------------------------------------------------------

# Rolling buffer length in STFT frames.  At 100 Hz, 17 frames ≈ 170 ms.
# The harmonic median filter needs enough history to distinguish sustained
# tones from transients; shorter buffers miss slower harmonics.
_HPSS_L_H: int = 17

# Frequency-axis median filter half-width in bins.  31 bins at 44100 Hz /
# 2048 window ≈ ±325 Hz of neighbourhood — wide enough to catch the broadband
# spread of a drum hit without swallowing narrow harmonic peaks.
_HPSS_L_P: int = 31


class PcmStft:
    """Rolling STFT over a stream of mono float32 samples.

    Accumulates samples in a ring buffer and emits one magnitude frame per hop.
    Call ``push()`` with each batch from ``SqueezeliteShmSource.read_new()``.

    Usage::

        stft = PcmStft(sample_rate=44100)
        frames = stft.push(mono_samples)   # list[np.ndarray], each shape (1025,)
    """

    def __init__(self, sample_rate: int) -> None:
        self._hop = round(sample_rate * 0.010)
        self._window = np.hamming(WINDOW_SIZE).astype(np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    @property
    def hop(self) -> int:
        """Number of samples between successive frames."""
        return self._hop

    @property
    def n_bins(self) -> int:
        """Number of real-valued frequency bins per frame (WINDOW_SIZE // 2 + 1)."""
        return WINDOW_SIZE // 2 + 1

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        """Append samples and return all complete magnitude frames.

        Each returned frame has shape (n_bins,) and dtype float32.
        Returns an empty list when fewer than WINDOW_SIZE samples are buffered.
        """
        self._buf = np.concatenate([self._buf, samples])
        frames: list[np.ndarray] = []
        while len(self._buf) >= WINDOW_SIZE:
            windowed = self._buf[:WINDOW_SIZE] * self._window
            mag = np.abs(np.fft.rfft(windowed)).astype(np.float32)
            frames.append(mag)
            self._buf = self._buf[self._hop:]
        return frames

    def reset(self) -> None:
        """Discard accumulated samples; clear STFT history for epoch transitions."""
        self._buf = np.zeros(0, dtype=np.float32)


# ---------------------------------------------------------------------------
# PcmHpss — Harmonic-Percussive Source Separation
# ---------------------------------------------------------------------------


class PcmHpss:
    """Real-time HPSS using vectorised 2D median filters on a rolling STFT buffer.

    Maintains a rolling buffer of _HPSS_L_H STFT magnitude frames and computes
    ``percussive_energy`` and ``harmonic_energy`` for the most-recently written
    frame on every call to ``push()``.

    Algorithm (Fitzgerald 2010 — numpy-only, no external deps):

    - Harmonic mask H²/(H²+P²) where H = bin-wise median along the time axis
      (a bin that is steady across _HPSS_L_H frames scores as harmonic).
    - Percussive mask P²/(H²+P²) where P = sliding median of width _HPSS_L_P
      across frequency bins of the current frame (a broadband spike is percussive).
    - The Wiener soft masks sum to 1.0, so percussive + harmonic ≈ 1.0.

    Both output values are normalised by the total frame energy and lie in
    [0, 1].  They degrade gracefully during the first _HPSS_L_H/2 frames while
    the circular buffer fills up from zeros — effects should check
    ``AudioFeatures.hpss_active`` and fall back to existing behaviour if needed.

    No external dependencies beyond numpy (no librosa, no scipy).  Benchmark on
    a development machine: ~924 µs/frame (9 % of a 10 ms frame budget) when
    vectorised via sliding_window_view.

    Usage::

        hpss = PcmHpss(sample_rate=44100)
        for new_samples in stream:
            for p_energy, h_energy in hpss.push(new_samples):
                ...   # p_energy + h_energy ≈ 1.0
    """

    def __init__(self, sample_rate: int) -> None:
        self._stft = PcmStft(sample_rate)
        n_bins = self._stft.n_bins
        self._buf = np.zeros((n_bins, _HPSS_L_H), dtype=np.float32)
        self._write_pos: int = 0
        self._half_p: int = _HPSS_L_P // 2

    def push(self, samples: np.ndarray) -> list[tuple[float, float]]:
        """Process PCM samples; return *(percussive_energy, harmonic_energy)* per frame.

        Both values are normalised fractions ∈ [0, 1] that sum to ≈ 1.0.
        Returns an empty list when the internal PcmStft has not yet accumulated
        enough samples for a complete STFT window.
        """
        results: list[tuple[float, float]] = []
        for frame in self._stft.push(samples):
            self._buf[:, self._write_pos] = frame
            self._write_pos = (self._write_pos + 1) % _HPSS_L_H

            center = self._buf[:, (self._write_pos - 1) % _HPSS_L_H]

            # Harmonic component: median across time axis (sustained = harmonic).
            H = np.median(self._buf, axis=1)

            # Percussive component: sliding median across frequency axis.
            padded = np.pad(center, (self._half_p, self._half_p), mode="edge")
            windows = np.lib.stride_tricks.sliding_window_view(padded, _HPSS_L_P)
            P_vals = np.median(windows, axis=1).astype(np.float32)

            H2 = H * H
            P2 = P_vals * P_vals
            denom = H2 + P2 + 1e-8
            mask_h = H2 / denom
            mask_p = P2 / denom

            total = float(np.sum(center)) + 1e-8
            results.append((
                float(np.dot(center, mask_p)) / total,
                float(np.dot(center, mask_h)) / total,
            ))
        return results


# ---------------------------------------------------------------------------
# AirPlayPipeSource — reads shairport-sync's named pipe
# ---------------------------------------------------------------------------

AIRPLAY_PIPE: Path = Path("/run/huesync/airplay.pcm")
AIRPLAY_SAMPLE_RATE: int = 44100
AIRPLAY_CHANNELS: int = 2
AIRPLAY_SAMPLE_WIDTH: int = 2  # bytes per sample, S16_LE
AIRPLAY_BYTES_PER_FRAME: int = AIRPLAY_CHANNELS * AIRPLAY_SAMPLE_WIDTH  # 4
_AIRPLAY_STALE_S: float = 2.0


class AirPlayPipeSource:
    """Reads raw PCM from shairport-sync's named pipe.

    Implements PcmSource. shairport-sync writes S16_LE stereo at 44100 Hz
    to AIRPLAY_PIPE; this class converts it to mono float32 using the same
    L+R average as SqueezeliteShmSource.

    The pipe is opened non-blocking so open() never stalls waiting for
    shairport-sync to connect. read_new() returns an empty array (EAGAIN)
    when shairport-sync has not yet started streaming.

    running returns True when audio data was received within the last
    _AIRPLAY_STALE_S seconds, enabling the UI to distinguish "waiting for
    AirPlay connection" from "receiving audio".
    """

    def __init__(self, path: Path = AIRPLAY_PIPE) -> None:
        self._path = path
        self._fd: int | None = None
        self._last_data_t: float | None = None
        self._remainder: bytes = b""

    def open(self) -> None:
        """Open the pipe non-blocking. Raises OSError if it does not exist."""
        self._fd = os.open(self._path, os.O_RDONLY | os.O_NONBLOCK)
        self._last_data_t = None
        self._remainder = b""

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    @property
    def sample_rate(self) -> int:
        return AIRPLAY_SAMPLE_RATE

    @property
    def running(self) -> bool:
        if self._last_data_t is None:
            return False
        return time.monotonic() - self._last_data_t < _AIRPLAY_STALE_S

    def read_new(self) -> np.ndarray:
        """Return mono float32 samples from the pipe, or empty if none available."""
        if self._fd is None:
            return np.empty(0, dtype=np.float32)
        try:
            raw = os.read(self._fd, 65536)  # up to 64 KB = ~370 ms @ 44100 Hz stereo
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                return np.empty(0, dtype=np.float32)
            raise
        if not raw:
            return np.empty(0, dtype=np.float32)

        # Prepend any sub-frame bytes carried from the previous read so that
        # partial frames never cause an L/R channel misalignment.
        combined = self._remainder + raw
        n_frames = len(combined) // AIRPLAY_BYTES_PER_FRAME
        if n_frames == 0:
            self._remainder = combined
            return np.empty(0, dtype=np.float32)

        self._remainder = combined[n_frames * AIRPLAY_BYTES_PER_FRAME :]
        self._last_data_t = time.monotonic()
        samples = np.frombuffer(combined[: n_frames * AIRPLAY_BYTES_PER_FRAME], dtype=np.int16)
        # Interleaved stereo s16 → mono float32 in [−1.0, 1.0].
        return (samples[0::2].astype(np.float32) + samples[1::2].astype(np.float32)) / (
            2.0 * 32768.0
        )


# ---------------------------------------------------------------------------
# SqueezeliteShmStereoSource — new stereo decoded-source adapter
# ---------------------------------------------------------------------------


class SqueezeliteShmStereoSource:
    """Reads stereo decoded float32 PCM from squeezelite's visualiser SHM.

    Alongside (not replacing) the legacy SqueezeliteShmSource.  Returns
    SourceReadResult with a stereo DecodedSourceFrame rather than a mono array.

    L and R channels are preserved separately — no (L+R)/2 downmix.

    Lifecycle:
    - n_new == 0 → TemporarilyNoData
    - torn read (writer moved during copy) → TemporarilyNoData (discard, retry)
    - n_new > VIS_BUF_SIZE // 2 (fell too far behind) → StreamInvalidated(UNKNOWN)
    - valid read → DataResult(DecodedSourceFrame)

    source_sample_pos is always None: the SHM buf_index is modular and cannot
    reconstruct an absolute timeline position.
    """

    def __init__(self) -> None:
        self._mm: mmap.mmap | None = None
        self._prev_index: int = 0
        self._source_id: str = ""

    def open(self, mac: str, *, _path: Path | None = None) -> None:
        """Map /dev/shm/squeezelite-{mac}.  ``_path`` overrides for tests."""
        path = _path if _path is not None else Path(f"/dev/shm/squeezelite-{mac}")
        fd = path.open("rb")
        try:
            self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE, access=mmap.ACCESS_READ)
        finally:
            fd.close()
        self._source_id = f"lms:{mac}"
        self._prev_index = self._read_header()[1]

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None

    def _read_header(self) -> tuple[int, int, bool, int, int]:
        if self._mm is None:
            raise RuntimeError("call open() before reading")
        self._mm.seek(_HDR_OFFSET)
        raw = self._mm.read(_HDR_SIZE)
        buf_size, buf_index, running_byte, rate, updated = struct.unpack(_HDR_FMT, raw)
        return buf_size, buf_index, bool(running_byte), rate, updated

    @property
    def sample_rate(self) -> int:
        return self._read_header()[3]

    @property
    def running(self) -> bool:
        return self._read_header()[2]

    @property
    def source_id(self) -> str:
        return self._source_id

    def read(self) -> SourceReadResult:
        """Return a SourceReadResult representing the latest available samples.

        Stereo layout: samples[:, 0] = L, samples[:, 1] = R.
        """
        _, buf_index, _, rate, _ = self._read_header()

        n_new = (buf_index - self._prev_index) % VIS_BUF_SIZE
        self._prev_index = buf_index

        if n_new == 0:
            return TemporarilyNoData()

        # Fell too far behind: continuity is no longer trustworthy.
        # Cannot prove an exact overrun, so known_lost_samples stays None.
        if n_new > VIS_BUF_SIZE // 2:
            _log.warning(
                "SHM stereo source fell behind: %d new samples (buffer %d); "
                "epoch invalidated.",
                n_new,
                VIS_BUF_SIZE,
            )
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        # Round down to complete stereo frames (2 scalar s16 samples per frame).
        n_new -= n_new % 2
        if n_new == 0:
            return TemporarilyNoData()

        start = (buf_index - n_new) % VIS_BUF_SIZE

        assert self._mm is not None
        if start + n_new <= VIS_BUF_SIZE:
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw = self._mm.read(n_new * 2)
        else:
            tail = VIS_BUF_SIZE - start
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(_BUF_OFFSET)
            raw_head = self._mm.read((n_new - tail) * 2)
            raw = raw_tail + raw_head

        # Seqlock-style consistency check: if the writer moved during our copy,
        # discard this block.  This is a transient race, not a proven continuity
        # break, so TemporarilyNoData is the correct result.
        _, buf_index_after, _, _, _ = self._read_header()
        if buf_index_after != buf_index:
            _log.debug(
                "SHM stereo source: torn read (buf_index %d → %d), discarding.",
                buf_index,
                buf_index_after,
            )
            return TemporarilyNoData()

        s16 = np.frombuffer(raw, dtype=np.int16)
        # Interleaved stereo s16: even indices = L, odd = R.
        # Scale to float32 in [−1.0, +1.0] by dividing each channel by 32768.
        # L and R are preserved separately — no downmix.
        left = s16[0::2].astype(np.float32) / 32768.0
        right = s16[1::2].astype(np.float32) / 32768.0
        stereo = np.column_stack([left, right])  # shape (n_frames, 2)

        # Check for non-finite values (s16 → float32 cannot produce NaN/Inf in
        # practice, but we validate for correctness).
        if not np.all(np.isfinite(stereo)):
            _log.warning("SHM stereo source: non-finite samples detected; invalidating epoch.")
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        over_range = bool(np.any(np.abs(stereo) >= 1.0))
        wall_ns = time.time_ns()

        frame = DecodedSourceFrame(
            samples=stereo,
            sample_rate=rate,
            channels=2,
            source_id=self._source_id,
            source_sample_pos=None,
            over_range=over_range,
            wall_ns=wall_ns,
        )
        return DataResult(frame=frame)


# ---------------------------------------------------------------------------
# AirPlayPipeStereoSource — new stereo decoded-source adapter
# ---------------------------------------------------------------------------


class AirPlayPipeStereoSource:
    """Reads stereo decoded float32 PCM from shairport-sync's named pipe.

    Alongside (not replacing) the legacy AirPlayPipeSource.  Returns
    SourceReadResult with a stereo DecodedSourceFrame.

    Source contract: 44100 Hz, S16_LE, 2 channels (stereo).
    L and R are preserved separately — no (L+R)/2 downmix.

    IMPORTANT — one ingress reader:
    Only one instance may read from the production FIFO at a time.  A FIFO
    is not broadcast.  Do not instantiate both this class and AirPlayPipeSource
    against the same path simultaneously.

    Lifecycle:
    - EAGAIN (no data) → TemporarilyNoData
    - EOF (write-end closed, iOS disconnected) → EndOfStream
    - Valid read → DataResult(DecodedSourceFrame)

    Partial-byte carry: sub-frame bytes from one read are prepended to the next
    so that L/R alignment is always preserved across read boundaries.
    """

    def __init__(self, path: Path = AIRPLAY_PIPE) -> None:
        self._path = path
        self._fd: int | None = None
        self._last_data_t: float | None = None
        self._remainder: bytes = b""
        self._source_id: str = f"airplay:{path}"

    def open(self) -> None:
        """Open the pipe non-blocking.  Raises OSError if it does not exist."""
        self._fd = os.open(self._path, os.O_RDONLY | os.O_NONBLOCK)
        self._last_data_t = None
        self._remainder = b""

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    @property
    def sample_rate(self) -> int:
        return AIRPLAY_SAMPLE_RATE

    @property
    def running(self) -> bool:
        if self._last_data_t is None:
            return False
        return time.monotonic() - self._last_data_t < _AIRPLAY_STALE_S

    @property
    def source_id(self) -> str:
        return self._source_id

    def read(self) -> SourceReadResult:
        """Return a SourceReadResult from the AirPlay pipe.

        Stereo layout: samples[:, 0] = L, samples[:, 1] = R.
        """
        if self._fd is None:
            return TemporarilyNoData()

        try:
            raw = os.read(self._fd, 65536)
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                return TemporarilyNoData()
            raise

        if not raw:
            # EOF: the write-end was closed (iOS disconnected from shairport-sync).
            return EndOfStream()

        # Prepend sub-frame carry from previous read to preserve L/R alignment.
        combined = self._remainder + raw
        n_frames = len(combined) // AIRPLAY_BYTES_PER_FRAME
        if n_frames == 0:
            self._remainder = combined
            return TemporarilyNoData()

        self._remainder = combined[n_frames * AIRPLAY_BYTES_PER_FRAME :]
        self._last_data_t = time.monotonic()

        s16 = np.frombuffer(combined[: n_frames * AIRPLAY_BYTES_PER_FRAME], dtype=np.int16)
        # Interleaved stereo S16_LE: even indices = L, odd = R.
        # Scale to float32 in [−1.0, +1.0] by dividing each channel by 32768.
        # L and R are preserved separately — no downmix.
        left = s16[0::2].astype(np.float32) / 32768.0
        right = s16[1::2].astype(np.float32) / 32768.0
        stereo = np.column_stack([left, right])  # shape (n_frames, 2)

        # Validate (s16 → float32 cannot produce NaN/Inf in practice).
        if not np.all(np.isfinite(stereo)):
            _log.warning("AirPlay stereo source: non-finite samples; invalidating epoch.")
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        over_range = bool(np.any(np.abs(stereo) >= 1.0))
        wall_ns = time.time_ns()

        frame = DecodedSourceFrame(
            samples=stereo,
            sample_rate=AIRPLAY_SAMPLE_RATE,
            channels=2,
            source_id=self._source_id,
            source_sample_pos=None,
            over_range=over_range,
            wall_ns=wall_ns,
        )
        return DataResult(frame=frame)


if __name__ == "__main__":
    # Quick benchmark: `python3 -m huesync.pcm_source`
    import timeit

    _rng = np.random.default_rng(42)
    _sr = 44100
    _hpss = PcmHpss(_sr)
    _hop = PcmStft(_sr).hop
    _chunk = _rng.standard_normal(_hop).astype(np.float32)
    # Warm up the STFT buffer so push() reliably yields frames.
    for _ in range(WINDOW_SIZE // _hop + 1):
        _hpss.push(_chunk)
    _n = 2000
    _elapsed = timeit.timeit(lambda: _hpss.push(_chunk), number=_n)
    _us = _elapsed / _n * 1e6
    print(f"PcmHpss.push() per frame: {_us:.1f} µs  ({10000/_us:.0f}x headroom vs 10 ms budget)")
