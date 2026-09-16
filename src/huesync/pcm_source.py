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

import enum
import errno
import logging
import mmap
import os
import struct
import time
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# Versioned SHM continuity ABI
# ---------------------------------------------------------------------------
# The legacy squeezelite vis_t header (ABI v0) provides only buf_index (wraps
# at VIS_BUF_SIZE) and updated (second-resolution timestamp).  These are
# insufficient to distinguish a full-lap overrun from no-new-data, or a same-
# second restart from ordinary polling.
#
# The extended ABI (v1) adds a 40-byte extension block immediately after the
# legacy header (at _V2_EXT_OFFSET = _HDR_OFFSET + _HDR_SIZE = 80).  When the
# magic 0x48555345 ('HUSE') is present at that offset, SqueezeliteShmSource
# reads the full extension and uses it for reliable continuity tracking.
# When absent the source falls back to heuristic v0 tracking.
#
# The v1 extension block layout (40 bytes at offset 80):
#   offset  0: uint32_t magic         = 0x48555345 'HUSE'
#   offset  4: uint16_t abi_version   = 1
#   offset  6: uint16_t flags         = reserved, currently 0
#   offset  8: uint32_t write_seq     = seqlock counter (even=stable, odd=writing)
#   offset 12: uint64_t generation    = random per-process ID (changes on restart)
#   offset 20: uint64_t abs_write_pos = exclusive next stereo-frame position
#   offset 28: uint64_t gap_seq       = monotonic gap counter (skipped exports)
#   offset 36: uint8_t  _pad[4]       = 0
#
# All values are little-endian.  abs_write_pos is measured in STEREO FRAMES,
# not scalar samples: one stereo frame is 2 * int16_t = 4 bytes.  This matches
# the natural unit of the DecodedSourceFrame contract downstream.
#
# The seqlock protocol lets a consumer read all fields atomically without
# taking a lock:
#   Writer: increment write_seq (odd), update fields, increment write_seq (even).
#   Reader: read write_seq1 → reject if odd; read fields; read write_seq2 →
#           accept only if seq1 == seq2 (i.e. writer did not run in between).
# ---------------------------------------------------------------------------

SHM_ABI_V1_MAGIC: int = 0x48555345  # 'HUSE' — marks extended header present
SHM_ABI_VERSION: int = 1

_V2_EXT_OFFSET: int = _HDR_OFFSET + _HDR_SIZE        # 80
# magic(4), abi_version(2), flags(2), write_seq(4),
# generation(8), abs_write_pos(8), gap_seq(8), pad(4)
_V2_EXT_FMT: str = "<IHHIQQQ4x"
_V2_EXT_SIZE: int = struct.calcsize(_V2_EXT_FMT)     # should be 40
assert _V2_EXT_SIZE == 40, f"_V2_EXT_SIZE={_V2_EXT_SIZE}, expected 40"
_BUF_OFFSET_V1: int = _V2_EXT_OFFSET + _V2_EXT_SIZE  # 120
_MMAP_SIZE_V1: int = _BUF_OFFSET_V1 + VIS_BUF_SIZE * 2  # 32888

# Maximum retries on a seqlock coherent-snapshot read.  Producer holds odd
# write_seq only for the few microseconds it takes to memcpy a small ring
# segment plus a handful of scalar stores; ten retries is generous.
MAX_SEQLOCK_RETRIES: int = 10


class ShmContinuityEvent(enum.Enum):
    """Classification of a single SqueezeliteShmSource.read_new_checked() call."""

    ADVANCE = "advance"            # normal: new samples, buf_index advanced <= VIS_BUF_SIZE//2
    NO_DATA = "no_data"            # buf_index unchanged (also: exact full-lap aliasing — see docs)
    TORN_READ = "torn_read"        # seqlock/snapshot inconsistency; samples discarded
    OVERRUN = "overrun"            # v0: buf_index advanced > VIS_BUF_SIZE//2 (fell behind writer)
    FULL_LAP = "full_lap"          # v1: abs delta == VIS_BUF_SIZE (exactly one full lap)
    MULTIPLE_LAPS = "multiple_laps"  # v1: abs delta > VIS_BUF_SIZE
    RESTART = "restart"            # generation/rate/running/abs regression signals new session
    SHM_REPLACED = "shm_replaced"  # buf_size field changed — SHM segment replaced
    GAP_SEQUENCE = "gap_sequence"  # v1: producer's gap_seq incremented (skipped export)
    UNSUPPORTED_ABI = "unsupported_abi"  # v0 encountered where v1 was required


@dataclass
class ShmReadResult:
    """Result of a single SHM read with continuity metadata."""

    samples: np.ndarray       # mono float32; empty on non-ADVANCE events
    event: ShmContinuityEvent
    abs_write_pos: int        # monotonic absolute write position in stereo frames
    n_delivered: int          # stereo frames returned
    n_lost: int               # estimated stereo frames lost (0 on clean advance)
    abi_version: int          # 0 = legacy, 1 = extended
    gap_seq: int = 0          # current v1 gap sequence (0 for v0)


@dataclass
class _ShmExtHeader:
    """Parsed v1 extension block."""

    magic: int
    abi_version: int
    flags: int          # reserved, currently 0
    write_seq: int      # seqlock counter (even=stable, odd=writer active)
    generation: int     # producer lifetime ID (random per process)
    abs_write_pos: int  # exclusive next stereo-frame position (monotonic)
    gap_seq: int        # monotonic gap sequence


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
        # Continuity tracking
        self._abi_version: int = 0                # detected on open()
        self._last_running: bool = False
        self._last_rate: int = 0
        self._last_buf_size: int = VIS_BUF_SIZE
        # Absolute write position in STEREO FRAMES.  v1: read directly from
        # the producer.  v0: estimated by accumulating (n_new // 2) each poll.
        self._abs_write_pos_frames: int = 0
        self._prev_generation: int = 0            # v1: producer lifetime ID
        self._prev_gap_seq: int = 0               # v1: last-seen skipped-export counter

    def open(self, mac: str, *, _path: Path | None = None) -> None:
        """Map /dev/shm/squeezelite-{mac} into memory.

        ``_path`` is an internal override used by tests; production code
        always passes a MAC address and lets this method derive the path.
        """
        path = _path if _path is not None else Path(f"/dev/shm/squeezelite-{mac}")
        fd = path.open("rb")
        try:
            # Try v1 size first; fall back to v0 if file is smaller.
            try:
                self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE_V1, access=mmap.ACCESS_READ)
                ext = self._read_ext_header()
                if ext is not None:
                    self._abi_version = ext.abi_version
                    self._abs_write_pos_frames = ext.abs_write_pos
                    self._prev_generation = ext.generation
                    self._prev_gap_seq = ext.gap_seq
                    _log.debug(
                        "SHM v1 ABI detected (generation=%d abs_write_pos=%d gap_seq=%d)",
                        ext.generation, ext.abs_write_pos, ext.gap_seq,
                    )
                else:
                    self._abi_version = 0
            except (ValueError, OSError):
                # File may be exactly v0 size; remap at v0 size.
                self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE, access=mmap.ACCESS_READ)
                self._abi_version = 0
        finally:
            fd.close()
        hdr = self._read_header()
        self._prev_index = hdr[1]
        self._last_running = hdr[2]
        self._last_rate = hdr[3]
        self._last_buf_size = hdr[0]

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

    def _read_ext_header(self) -> _ShmExtHeader | None:
        """Read the v1 extension block; return None if magic absent or mmap too small."""
        if self._mm is None:
            return None
        if len(self._mm) < _V2_EXT_OFFSET + _V2_EXT_SIZE:
            return None
        self._mm.seek(_V2_EXT_OFFSET)
        raw = self._mm.read(_V2_EXT_SIZE)
        magic, abi_ver, flags, write_seq, generation, abs_write_pos, gap_seq = struct.unpack(
            _V2_EXT_FMT, raw
        )
        if magic != SHM_ABI_V1_MAGIC:
            return None
        return _ShmExtHeader(
            magic=magic,
            abi_version=abi_ver,
            flags=flags,
            write_seq=write_seq,
            generation=generation,
            abs_write_pos=abs_write_pos,
            gap_seq=gap_seq,
        )

    def _read_ext_coherent(self) -> _ShmExtHeader | None:
        """Read v1 extension using the seqlock retry protocol.

        Returns None when:
        - The magic is absent or the mmap is too small (no v1 header).
        - We could not obtain a stable coherent snapshot within
          MAX_SEQLOCK_RETRIES attempts (producer is writing continuously
          faster than we can read the extension block).

        The caller should treat a coherent-read failure as TORN_READ.
        """
        for _ in range(MAX_SEQLOCK_RETRIES):
            ext = self._read_ext_header()
            if ext is None:
                return None
            if ext.write_seq % 2 == 1:
                # Producer is mid-write; retry.
                continue
            seq1 = ext.write_seq
            ext2 = self._read_ext_header()
            if ext2 is None:
                return None
            if ext2.write_seq != seq1:
                # A write happened between our two reads; retry.
                continue
            return ext
        return None

    def read_new_checked(self) -> ShmReadResult:
        """Read new PCM samples with full continuity classification.

        Returns ShmReadResult with:
          - samples: mono float32 (empty on non-ADVANCE events)
          - event: continuity classification
          - abs_write_pos: monotonic write position
          - n_delivered / n_lost: accounting

        Use this in preference to read_new() when the caller needs to signal
        StreamInvalidated on continuity breaks (full-lap, restart, SHM replaced).
        """
        buf_size, buf_index, running, rate, updated = self._read_header()

        # SHM replacement: buf_size field changed since last open.
        if buf_size != self._last_buf_size:
            self._last_buf_size = buf_size
            self._prev_index = buf_index
            self._last_running = running
            self._last_rate = rate
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.SHM_REPLACED,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )

        # Restart detection: running or rate changed.
        prev_running = self._last_running
        self._last_running = running
        if rate != self._last_rate:
            self._last_rate = rate
            self._prev_index = buf_index
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.RESTART,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )
        if not prev_running and running:
            self._prev_index = buf_index
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.RESTART,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )

        # v1: use coherent seqlock read of abs_write_pos for precise continuity.
        if self._abi_version >= 1:
            ext = self._read_ext_coherent()
            if ext is None:
                # Either the header disappeared (SHM was truncated) or the
                # producer wrote continuously across every retry attempt.
                # Either way we cannot trust anything read this poll — emit
                # TORN_READ so the caller resets the epoch and starts fresh.
                self._prev_index = buf_index
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=ShmContinuityEvent.TORN_READ,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=0,
                    abi_version=self._abi_version,
                    gap_seq=self._prev_gap_seq,
                )

            # Generation change: producer restarted between polls.
            if self._prev_generation != 0 and ext.generation != self._prev_generation:
                self._prev_generation = ext.generation
                self._abs_write_pos_frames = ext.abs_write_pos
                self._prev_gap_seq = ext.gap_seq
                self._prev_index = buf_index
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=ShmContinuityEvent.RESTART,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=0,
                    abi_version=self._abi_version,
                    gap_seq=self._prev_gap_seq,
                )
            self._prev_generation = ext.generation

            # Delta measured in stereo frames.
            delta_frames = int(ext.abs_write_pos) - int(self._abs_write_pos_frames)
            if delta_frames < 0:
                # Backward regression: producer restarted with reset counter
                # (or was rebuilt).  Treat as restart.
                self._abs_write_pos_frames = ext.abs_write_pos
                self._prev_gap_seq = ext.gap_seq
                self._prev_index = buf_index
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=ShmContinuityEvent.RESTART,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=0,
                    abi_version=self._abi_version,
                    gap_seq=self._prev_gap_seq,
                )

            # Gap-sequence increment: producer skipped one or more exports
            # (its trywrlock failed).  Report once per unique gap_seq value.
            if ext.gap_seq != self._prev_gap_seq:
                self._prev_gap_seq = ext.gap_seq
                self._abs_write_pos_frames = ext.abs_write_pos
                self._prev_index = buf_index
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=ShmContinuityEvent.GAP_SEQUENCE,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=0,
                    abi_version=self._abi_version,
                    gap_seq=ext.gap_seq,
                )

            if delta_frames == 0:
                # No progress since last poll.  Do not adopt buf_index — the
                # ring buffer position must remain paired with abs_write_pos.
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=ShmContinuityEvent.NO_DATA,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=0,
                    abi_version=self._abi_version,
                    gap_seq=ext.gap_seq,
                )

            # A stereo frame is 2 int16 samples in the ring buffer.
            # VIS_BUF_SIZE is measured in scalar int16 samples, so its
            # equivalent in stereo frames is VIS_BUF_SIZE // 2.
            frame_capacity = VIS_BUF_SIZE // 2
            if delta_frames >= frame_capacity:
                n_lost = int(delta_frames) - frame_capacity
                event = (
                    ShmContinuityEvent.MULTIPLE_LAPS
                    if delta_frames > frame_capacity
                    else ShmContinuityEvent.FULL_LAP
                )
                self._abs_write_pos_frames = ext.abs_write_pos
                self._prev_index = buf_index
                return ShmReadResult(
                    samples=np.empty(0, dtype=np.float32),
                    event=event,
                    abs_write_pos=self._abs_write_pos_frames,
                    n_delivered=0,
                    n_lost=n_lost,
                    abi_version=self._abi_version,
                    gap_seq=ext.gap_seq,
                )

            # Adopt the coherent snapshot and continue to the buf_index path
            # below.  abs_write_pos_frames is not updated yet — we set it
            # after a successful PCM copy so any torn read leaves state alone.
            new_abs_frames = int(ext.abs_write_pos)
        else:
            new_abs_frames = None

        # v0 heuristic (or v1 fall-through): compute n_new from buf_index.
        n_new = (buf_index - self._prev_index) % VIS_BUF_SIZE
        if n_new == 0:
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.NO_DATA,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )

        if n_new > VIS_BUF_SIZE // 2:
            # Overrun: fell more than half buffer behind (v0 heuristic).
            n_lost = n_new - VIS_BUF_SIZE // 2
            self._prev_index = buf_index
            # Advance consumer's stereo-frame counter by best estimate.
            self._abs_write_pos_frames += n_new // 2
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.OVERRUN,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=n_lost // 2,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )

        n_new -= n_new % 2  # round down to complete stereo frames (in scalar units)
        if n_new == 0:
            self._prev_index = buf_index
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.ADVANCE,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )
        start = (buf_index - n_new) % VIS_BUF_SIZE
        buf_offset = _BUF_OFFSET_V1 if self._abi_version >= 1 else _BUF_OFFSET
        assert self._mm is not None
        if start + n_new <= VIS_BUF_SIZE:
            self._mm.seek(buf_offset + start * 2)
            raw = self._mm.read(n_new * 2)
        else:
            tail = VIS_BUF_SIZE - start
            self._mm.seek(buf_offset + start * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(buf_offset)
            raw_head = self._mm.read((n_new - tail) * 2)
            raw = raw_tail + raw_head
        _, buf_index_after, _, _, _ = self._read_header()
        if buf_index_after != buf_index:
            self._prev_index = buf_index_after
            return ShmReadResult(
                samples=np.empty(0, dtype=np.float32),
                event=ShmContinuityEvent.TORN_READ,
                abs_write_pos=self._abs_write_pos_frames,
                n_delivered=0,
                n_lost=0,
                abi_version=self._abi_version,
                gap_seq=self._prev_gap_seq,
            )
        self._prev_index = buf_index
        # Commit position after a clean read.
        if new_abs_frames is not None:
            self._abs_write_pos_frames = new_abs_frames
        else:
            self._abs_write_pos_frames += n_new // 2
        samples_i16 = np.frombuffer(raw, dtype=np.int16)
        mono = (
            samples_i16[0::2].astype(np.float32) + samples_i16[1::2].astype(np.float32)
        ) / (2.0 * 32768.0)
        return ShmReadResult(
            samples=mono,
            event=ShmContinuityEvent.ADVANCE,
            abs_write_pos=self._abs_write_pos_frames,
            n_delivered=len(mono),
            n_lost=0,
            abi_version=self._abi_version,
            gap_seq=self._prev_gap_seq,
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
    - torn read (writer moved during copy) → StreamInvalidated(UNKNOWN)
    - n_new > VIS_BUF_SIZE // 2 (fell too far behind) → StreamInvalidated(UNKNOWN)
    - valid read → DataResult(DecodedSourceFrame)

    source_sample_pos is always None: the SHM buf_index is modular and cannot
    reconstruct an absolute timeline position.
    """

    def __init__(self) -> None:
        self._mm: mmap.mmap | None = None
        self._path: Path | None = None
        self._prev_index: int = 0
        self._prev_running: bool = False
        self._prev_rate: int = 0
        self._prev_updated: int = 0
        self._source_id: str = ""
        # v1 ABI continuity state
        self._abi_version: int = 0
        self._prev_generation: int = 0
        self._abs_write_pos_frames: int = 0  # in stereo frames
        self._prev_gap_seq: int = 0
        # (st_dev, st_ino) at the time of mmap.  Used to detect SHM
        # replacement (e.g. squeezelite recreated the segment with a fresh
        # inode while our mmap kept pointing at the unlinked file).  See
        # _detect_shm_replacement().
        self._shm_dev_ino: tuple[int, int] | None = None
        # ABI is considered unsupported once we see a v1-magic block whose
        # abi_version is not equal to SHM_ABI_VERSION.  Subsequent reads
        # short-circuit to StreamInvalidated so the caller can rebuild.
        self._unsupported_abi: bool = False

    def open(
        self,
        mac: str,
        *,
        _path: Path | None = None,
        require_v1: bool = True,
    ) -> None:
        """Map /dev/shm/squeezelite-{mac}.  ``_path`` overrides for tests.

        When ``require_v1`` is True (the default for the production canonical
        LMS PCM path), the SHM segment MUST expose the HueSync v1 extension
        header at offset 80 (magic 0x48555345 'HUSE') AND advertise the
        exact ABI version this consumer supports (SHM_ABI_VERSION == 1).
        A stock squeezelite exposes only the legacy v0 header, and a future
        producer running a newer v2 layout would be silently misinterpreted
        by this reader — activation is therefore rejected in either case
        with an actionable RuntimeError explaining how to rebuild
        squeezelite.  Tests that need to exercise the v0 fall-through code
        path may pass ``require_v1=False`` explicitly.
        """
        path = _path if _path is not None else Path(f"/dev/shm/squeezelite-{mac}")
        self._path = path
        fd = path.open("rb")
        try:
            # Try v1 size first; fall back to v0 if file is smaller.
            try:
                self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE_V1, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE, access=mmap.ACCESS_READ)
            # Record the (st_dev, st_ino) tuple identifying this SHM segment
            # so subsequent reads can detect that the producer replaced the
            # segment (new inode, same path) while we still hold the old
            # mmap.  Stat the open descriptor, not the path, so we observe
            # the file the mmap actually refers to.
            try:
                st = os.fstat(fd.fileno())
                self._shm_dev_ino = (st.st_dev, st.st_ino)
            except OSError:
                self._shm_dev_ino = None
        finally:
            fd.close()
        self._source_id = f"lms:{mac}"

        ext = self._read_ext_header()
        if ext is None or ext.magic != SHM_ABI_V1_MAGIC:
            if require_v1:
                self.close()
                raise RuntimeError(
                    f"Squeezelite SHM at {path} does not provide the v1 ABI "
                    f"(magic 0x{SHM_ABI_V1_MAGIC:08X} not found at offset "
                    f"{_V2_EXT_OFFSET}). "
                    "Rebuild squeezelite with the HueSync v1 producer patch "
                    "from squeezelite/output_vis_v1.c and redeploy."
                )
            self._abi_version = 0
        else:
            # Exact ABI version match.  A future producer running v2 has a
            # different layout and cannot be interpreted safely — surface it
            # as an actionable RuntimeError rather than a torn-read
            # heuristic, matching the require_v1=False fallback branch which
            # would otherwise happily downgrade to v0 semantics.
            if ext.abi_version != SHM_ABI_VERSION:
                if require_v1:
                    self.close()
                    raise RuntimeError(
                        f"Squeezelite SHM at {path} advertises ABI version "
                        f"{ext.abi_version}, but this consumer only supports "
                        f"version {SHM_ABI_VERSION}.  Rebuild squeezelite "
                        "against the HueSync v1 producer patch, or upgrade "
                        "HueSync to a matching consumer."
                    )
                self._unsupported_abi = True
                self._abi_version = 0
            else:
                self._abi_version = ext.abi_version
                self._prev_generation = ext.generation
                self._abs_write_pos_frames = ext.abs_write_pos
                self._prev_gap_seq = ext.gap_seq

        _, buf_index, running, rate, updated = self._read_header()
        self._prev_index = buf_index
        self._prev_running = running
        self._prev_rate = rate
        self._prev_updated = updated

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

    def _read_ext_header(self) -> _ShmExtHeader | None:
        """Read the v1 extension block; return None if magic absent or mmap too small."""
        if self._mm is None:
            return None
        if len(self._mm) < _V2_EXT_OFFSET + _V2_EXT_SIZE:
            return None
        self._mm.seek(_V2_EXT_OFFSET)
        raw = self._mm.read(_V2_EXT_SIZE)
        magic, abi_ver, flags, write_seq, generation, abs_write_pos, gap_seq = struct.unpack(
            _V2_EXT_FMT, raw
        )
        return _ShmExtHeader(
            magic=magic,
            abi_version=abi_ver,
            flags=flags,
            write_seq=write_seq,
            generation=generation,
            abs_write_pos=abs_write_pos,
            gap_seq=gap_seq,
        )

    def _read_ext_coherent(self) -> _ShmExtHeader | None:
        """Seqlock retry loop matching SqueezeliteShmSource._read_ext_coherent.

        Reject snapshots whose abi_version does not match SHM_ABI_VERSION —
        a future producer would corrupt continuity accounting silently.
        """
        for _ in range(MAX_SEQLOCK_RETRIES):
            ext = self._read_ext_header()
            if ext is None or ext.magic != SHM_ABI_V1_MAGIC:
                return None
            if ext.abi_version != SHM_ABI_VERSION:
                # A newer producer wrote a layout we do not understand.
                # Surface the mismatch so the caller can invalidate.
                return None
            if ext.write_seq % 2 == 1:
                continue
            seq1 = ext.write_seq
            ext2 = self._read_ext_header()
            if ext2 is None or ext2.write_seq != seq1:
                continue
            return ext
        return None

    def _detect_shm_replacement(self) -> bool:
        """Return True when the file at ``self._path`` no longer matches the
        (dev, ino) tuple recorded at open() time.

        squeezelite recreates its SHM segment with a fresh inode on restart
        (the old file is unlinked and a new one created at the same path).
        Our existing mmap keeps referring to the old, now-orphan inode until
        we notice and remap.  A stat on the path each read cycle catches the
        swap the first time it happens.
        """
        if self._path is None or self._shm_dev_ino is None:
            return False
        try:
            st = os.stat(self._path)
        except OSError:
            # The file was unlinked before the producer recreated it.  Treat
            # that as a replacement too: the mmap we hold no longer maps a
            # live producer.
            return True
        return (st.st_dev, st.st_ino) != self._shm_dev_ino

    @property
    def sample_rate(self) -> int:
        return self._read_header()[3]

    @property
    def running(self) -> bool:
        return self._read_header()[2]

    @property
    def source_id(self) -> str:
        return self._source_id

    def _buf_offset(self) -> int:
        """Ring-buffer PCM starts after the extension block on v1, at 80 on v0."""
        return _BUF_OFFSET_V1 if self._abi_version >= 1 else _BUF_OFFSET

    def _read_v1(self) -> SourceReadResult:
        """v1 read path: coherent snapshot + producer continuity classification."""
        assert self._mm is not None
        ext = self._read_ext_coherent()
        if ext is None:
            return StreamInvalidated(
                cause=InvalidationCause.UNKNOWN, known_lost_samples=None
            )

        # Rate change → new epoch.
        _, buf_index_before, running, rate, updated = self._read_header()
        if self._prev_rate != 0 and rate != self._prev_rate:
            _log.warning(
                "SHM stereo source: sample rate changed (%d → %d); invalidating epoch.",
                self._prev_rate, rate,
            )
            self._prev_rate = rate
            self._prev_running = running
            self._prev_index = buf_index_before
            self._prev_generation = ext.generation
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_gap_seq = ext.gap_seq
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        # running transition False → True: new session.
        if running and not self._prev_running:
            self._prev_running = running
            self._prev_rate = rate
            self._prev_index = buf_index_before
            self._prev_generation = ext.generation
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_gap_seq = ext.gap_seq
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)
        self._prev_running = running
        self._prev_rate = rate

        # Generation change: producer restarted.
        if self._prev_generation != 0 and ext.generation != self._prev_generation:
            self._prev_generation = ext.generation
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_gap_seq = ext.gap_seq
            self._prev_index = buf_index_before
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)
        self._prev_generation = ext.generation

        # abs_write_pos backward regression: producer reset counter.
        delta_frames = int(ext.abs_write_pos) - int(self._abs_write_pos_frames)
        if delta_frames < 0:
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_gap_seq = ext.gap_seq
            self._prev_index = buf_index_before
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        # Gap sequence advanced → producer skipped exports; invalidate epoch.
        if ext.gap_seq != self._prev_gap_seq:
            self._prev_gap_seq = ext.gap_seq
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_index = buf_index_before
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        if delta_frames == 0:
            return TemporarilyNoData()

        frame_capacity = VIS_BUF_SIZE // 2  # ring capacity in stereo frames
        if delta_frames >= frame_capacity:
            # A full lap (or more) has been overwritten between polls.
            self._abs_write_pos_frames = ext.abs_write_pos
            self._prev_index = buf_index_before
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        # Report the absolute stereo-frame position at the START of this read.
        start_abs_pos = int(self._abs_write_pos_frames)

        # Copy the new PCM window from the ring buffer.  Sample counts here
        # are in scalar int16 units so the arithmetic matches the ring layout.
        n_new_scalar = delta_frames * 2
        start_scalar = (buf_index_before - n_new_scalar) % VIS_BUF_SIZE
        buf_offset = self._buf_offset()
        if start_scalar + n_new_scalar <= VIS_BUF_SIZE:
            self._mm.seek(buf_offset + start_scalar * 2)
            raw = self._mm.read(n_new_scalar * 2)
        else:
            tail = VIS_BUF_SIZE - start_scalar
            self._mm.seek(buf_offset + start_scalar * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(buf_offset)
            raw_head = self._mm.read((n_new_scalar - tail) * 2)
            raw = raw_tail + raw_head

        # Final seqlock verification: refuse the block if a writer ran while
        # we were copying.  A single retry loop already handled the metadata
        # coherence; this catches races against the PCM copy itself.
        #
        # The full snapshot spans (metadata read, PCM copy, metadata re-read)
        # so verify EVERY authoritative field agrees:
        #   - magic must still be present (segment not truncated)
        #   - abi_version unchanged (producer not rewritten mid-copy)
        #   - write_seq matches AND is even (no writer since the coherent read)
        #   - generation matches (producer did not restart during the copy)
        #   - abs_write_pos matches (no writer since the coherent read)
        ext_after = self._read_ext_header()
        if (
            ext_after is None
            or ext_after.magic != SHM_ABI_V1_MAGIC
            or ext_after.abi_version != SHM_ABI_VERSION
            or ext_after.write_seq % 2 == 1
            or ext_after.write_seq != ext.write_seq
            or ext_after.generation != ext.generation
            or ext_after.abs_write_pos != ext.abs_write_pos
        ):
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        s16 = np.frombuffer(raw, dtype=np.int16)
        left = s16[0::2].astype(np.float32) / 32768.0
        right = s16[1::2].astype(np.float32) / 32768.0
        stereo = np.column_stack([left, right])

        if not np.all(np.isfinite(stereo)):
            _log.warning("SHM stereo source: non-finite samples; invalidating epoch.")
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        over_range = bool(np.any(np.abs(stereo) >= 1.0))
        wall_ns = time.time_ns()

        # Commit position now that the copy verified.
        self._abs_write_pos_frames = int(ext.abs_write_pos)
        self._prev_index = buf_index_before
        self._prev_updated = updated

        frame = DecodedSourceFrame(
            samples=stereo,
            sample_rate=rate,
            channels=2,
            source_id=self._source_id,
            source_sample_pos=start_abs_pos,
            over_range=over_range,
            wall_ns=wall_ns,
        )
        return DataResult(frame=frame)

    def _read_v0(self) -> SourceReadResult:
        """v0 fall-through: buf_index heuristic, no producer continuity signal."""
        assert self._mm is not None
        _, buf_index, running, rate, updated = self._read_header()

        # Sample-rate change: squeezelite restarted or a new track at a different rate.
        if self._prev_rate != 0 and rate != self._prev_rate:
            _log.warning(
                "SHM stereo source: sample rate changed (%d → %d); invalidating epoch.",
                self._prev_rate, rate,
            )
            self._prev_index = buf_index
            self._prev_running = running
            self._prev_rate = rate
            self._prev_updated = updated
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        # Running transition False→True: squeezelite restarted and is writing again.
        if running and not self._prev_running:
            _log.debug("SHM stereo source: running transition → True; invalidating epoch.")
            self._prev_index = buf_index
            self._prev_running = running
            self._prev_rate = rate
            self._prev_updated = updated
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        self._prev_running = running
        self._prev_rate = rate

        raw_delta = (buf_index - self._prev_index) % VIS_BUF_SIZE

        if raw_delta == 0:
            if updated != self._prev_updated:
                _log.warning(
                    "SHM stereo source: full-lap aliasing detected "
                    "(buf_index unchanged, writer timestamp advanced); invalidating epoch."
                )
                self._prev_index = buf_index
                self._prev_updated = updated
                return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)
            self._prev_updated = updated
            return TemporarilyNoData()

        self._prev_index = buf_index
        self._prev_updated = updated

        if raw_delta > VIS_BUF_SIZE // 2:
            _log.warning(
                "SHM stereo source fell behind: %d new samples (buffer %d); "
                "epoch invalidated.",
                raw_delta,
                VIS_BUF_SIZE,
            )
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        n_new = raw_delta - raw_delta % 2
        if n_new == 0:
            return TemporarilyNoData()

        start = (buf_index - n_new) % VIS_BUF_SIZE
        buf_offset = self._buf_offset()
        if start + n_new <= VIS_BUF_SIZE:
            self._mm.seek(buf_offset + start * 2)
            raw = self._mm.read(n_new * 2)
        else:
            tail = VIS_BUF_SIZE - start
            self._mm.seek(buf_offset + start * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(buf_offset)
            raw_head = self._mm.read((n_new - tail) * 2)
            raw = raw_tail + raw_head

        _, buf_index_after, _, _, _ = self._read_header()
        if buf_index_after != buf_index:
            _log.debug(
                "SHM stereo source: torn read (buf_index %d → %d); "
                "audio gap, invalidating epoch.",
                buf_index, buf_index_after,
            )
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        s16 = np.frombuffer(raw, dtype=np.int16)
        left = s16[0::2].astype(np.float32) / 32768.0
        right = s16[1::2].astype(np.float32) / 32768.0
        stereo = np.column_stack([left, right])

        if not np.all(np.isfinite(stereo)):
            _log.warning("SHM stereo source: non-finite samples detected; invalidating epoch.")
            return StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=None)

        over_range = bool(np.any(np.abs(stereo) >= 1.0))
        wall_ns = time.time_ns()

        # Advance consumer-side stereo-frame counter for downstream consumers.
        start_abs = int(self._abs_write_pos_frames)
        self._abs_write_pos_frames += n_new // 2

        frame = DecodedSourceFrame(
            samples=stereo,
            sample_rate=rate,
            channels=2,
            source_id=self._source_id,
            source_sample_pos=start_abs,
            over_range=over_range,
            wall_ns=wall_ns,
        )
        return DataResult(frame=frame)

    def read(self) -> SourceReadResult:
        """Return a SourceReadResult representing the latest available samples.

        Stereo layout: samples[:, 0] = L, samples[:, 1] = R.  When the v1 ABI
        is present (the production default) the read is coherent under a
        seqlock and stream continuity is authoritative rather than heuristic.
        """
        # If open() detected an unsupported v1 ABI version and the caller
        # elected to continue anyway (require_v1=False), every subsequent
        # read must surface as an invalidation — there is no safe fallback.
        if self._unsupported_abi:
            return StreamInvalidated(
                cause=InvalidationCause.UNKNOWN, known_lost_samples=None
            )
        # Detect SHM segment replacement: squeezelite's post-restart segment
        # has a different inode at the same path.  Our mmap still maps the
        # old (unlinked) file, so continue reading it would silently return
        # stale data.  Invalidate the epoch and clear the (dev, ino) tuple
        # so the caller can re-open at its own cadence.
        if self._detect_shm_replacement():
            self._shm_dev_ino = None
            return StreamInvalidated(
                cause=InvalidationCause.UNKNOWN, known_lost_samples=None
            )
        if self._abi_version >= 1:
            return self._read_v1()
        return self._read_v0()


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
            # Discard any partial-byte carry: it belongs to the just-ended stream
            # and must not prefix the next reconnect's audio.
            self._remainder = b""
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
