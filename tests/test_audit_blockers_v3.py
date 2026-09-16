"""Regression tests for the round-3 audit blockers against ce6a0f3.

Every test in this file is designed to FAIL on commit
``ce6a0f3144a4f79dac4aa5fae7aeb7e55cf6d0ac`` and PASS after the
round-3 fixes.  The blockers covered:

  1. Retiring-worker ownership on ``replace_analyser`` timeout — the
     old worker must not have a second concurrent reader on the same
     source, must remain owned by ``SyncEngine.stop()``, and the
     runtime must not silently install a deactivated sentinel while
     the manager/session still report active.
  2. Cross-call publication ordering — records must not go backwards
     across ``feed()`` calls, and no publication may carry
     ``_last_bars`` from a spectrum update whose interval sits in the
     future of the record's own interval.
  3. Unsupported-ABI recovery must be mapping-scoped — a rejected v2
     inode must not permanently poison the SqueezeliteShmStereoSource
     instance; a subsequent valid-v1 replacement must recover.
  4. Producer init must publish an odd ``write_seq`` regardless of
     the prior segment contents (shm_open O_CREAT | O_RDWR may reuse
     an existing segment whose ``write_seq`` is already odd).
"""

from __future__ import annotations

import re
import struct
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from huesync.canonicalizer import (
    StreamInvalidated,
    TemporarilyNoData,
)
from huesync.models import Profile
from huesync.pcm_source import (
    SHM_ABI_V1_MAGIC,
    SHM_ABI_VERSION,
    SqueezeliteShmStereoSource,
)
from huesync.spectrum_engine import (
    ProcessorUpdate,
    V2SpectrumEngine,
)
from huesync.sync_engine import (
    CanonicalAnalysisPipeline,
    SyncEngine,
)

# ---------------------------------------------------------------------------
# BLOCKER 4: init sequence must be ODD regardless of prior contents
# ---------------------------------------------------------------------------


_PATCH = Path(__file__).resolve().parents[1] / "squeezelite" / "output_vis_v1.patch"
_VIS_C = Path(__file__).resolve().parents[1] / "squeezelite" / "output_vis_v1.c"
_VIS_H = Path(__file__).resolve().parents[1] / "squeezelite" / "vis_shm_v1.h"


def test_producer_declares_begin_init_and_finish_init():
    """The two-phase init helpers ``vis_shm_v1_begin_init`` and
    ``vis_shm_v1_finish_init`` must be declared in the header.

    Would FAIL on ce6a0f3: only ``vis_shm_v1_init`` existed and it
    unconditionally wrote ``ext->write_seq = 1``, ignoring reused SHM.
    """
    header = _VIS_H.read_text()
    assert "vis_shm_v1_begin_init" in header
    assert "vis_shm_v1_finish_init" in header


def test_producer_begin_init_reads_prev_seq_then_forces_odd():
    """``vis_shm_v1_begin_init`` must read the current write_seq and
    compute an odd successor — never do an unconditional store of 1.

    Would FAIL on ce6a0f3: ``vis_shm_v1_init`` did ``ext->write_seq =
    1`` unconditionally.  For a reused segment whose write_seq was
    e.g. 3 that store is monotonically NON-increasing and, more
    critically, ignores the "smallest odd > current" invariant the
    round-3 audit demands.
    """
    src = _VIS_C.read_text()
    m = re.search(
        r"void\s+vis_shm_v1_begin_init\s*\([\s\S]*?\n\}",
        src,
    )
    assert m is not None, "vis_shm_v1_begin_init missing in output_vis_v1.c"
    body = m.group(0)
    assert "__atomic_load_n" in body, (
        "begin_init must READ the current write_seq before deciding what to store"
    )
    # Must reference some ODD-forcing arithmetic (either `& 1` or
    # `% 2` or `cur + 1`/`cur + 2`).
    assert re.search(r"cur\s*\+\s*[12]", body) or re.search(r"\|\s*1", body), (
        "begin_init must compute an odd successor of the current write_seq"
    )


def test_producer_patch_calls_begin_init_before_legacy_writes():
    """The init hunk must call ``vis_shm_v1_begin_init`` BEFORE writing
    ``buf_size / running / rate`` (the legacy header snapshot fields).
    """
    body = _PATCH.read_text()
    hunk_match = re.search(
        r"pthread_rwlock_init\([\s\S]*?LOG_INFO\(\"opened",
        body,
    )
    assert hunk_match is not None
    hunk = hunk_match.group(0)
    begin_idx = hunk.find("vis_shm_v1_begin_init")
    running_idx = hunk.find("vis_mmap->running = false;")
    assert begin_idx >= 0, "init hunk must call vis_shm_v1_begin_init"
    assert running_idx >= 0
    assert begin_idx < running_idx, (
        "vis_shm_v1_begin_init must precede legacy field writes"
    )


# ---------------------------------------------------------------------------
# BLOCKER 3: unsupported ABI must not permanently poison the source
# ---------------------------------------------------------------------------


_V1_MMAP_SIZE = 32888   # 80 + 40 + 32768
_V2_EXT_OFFSET = 80
_HDR_FMT = "<IIBxxxIQ"


def _write_v1_segment(
    path: Path,
    *,
    magic: int = SHM_ABI_V1_MAGIC,
    abi_version: int = SHM_ABI_VERSION,
    write_seq: int = 2,
    generation: int = 0xDEADBEEF,
    abs_write_pos: int = 0,
    gap_seq: int = 0,
) -> None:
    with open(path, "wb") as f:
        f.write(b"\x00" * _V1_MMAP_SIZE)
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(struct.pack(_HDR_FMT, 16384, 0, 1, 48000, 0))
        f.seek(_V2_EXT_OFFSET)
        f.write(
            struct.pack(
                "<IHHIQQQ4x",
                magic,
                abi_version,
                0,
                write_seq,
                generation,
                abs_write_pos,
                gap_seq,
            )
        )


def _write_v0_segment(path: Path) -> None:
    with open(path, "wb") as f:
        f.write(b"\x00" * 32848)
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(struct.pack(_HDR_FMT, 16384, 0, 1, 48000, 0))


def test_shm_v2_then_new_v1_recovers_automatically(tmp_path):
    """v1 → v2 → new valid v1: the source must adopt the new inode
    without the caller having to close+reopen.

    Would FAIL on ce6a0f3: ``self._unsupported_abi`` short-circuited
    ``read()`` before ``_detect_shm_replacement`` could observe the
    new inode.
    """
    path = tmp_path / "shm"
    _write_v1_segment(path, generation=0x1111)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    # Replace with unsupported v2 ABI.
    path.unlink()
    _write_v1_segment(path, abi_version=SHM_ABI_VERSION + 1)
    r1 = src.read()
    assert isinstance(r1, StreamInvalidated)
    assert src._unsupported_abi is True

    # Replace again with a valid v1 (different inode → detect_replacement
    # returns True; remap clears _unsupported_abi and adopts).
    path.unlink()
    _write_v1_segment(path, generation=0x2222)
    r2 = src.read()
    assert isinstance(r2, StreamInvalidated)   # epoch break on remap
    assert src._unsupported_abi is False, (
        "unsupported ABI flag must be cleared when a valid new inode "
        "appears at the same path"
    )
    assert src._abi_version == SHM_ABI_VERSION
    assert src._prev_generation == 0x2222
    src.close()


def test_shm_v0_then_new_v1_recovers_automatically(tmp_path):
    """v1 → v0 → new valid v1: same recovery path via the pending-remap
    state machine.  v0 is never accepted under require_v1=True."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    # Replace with legacy v0 (no v1 magic).
    path.unlink()
    _write_v0_segment(path)
    r1 = src.read()
    assert isinstance(r1, StreamInvalidated)
    assert src._pending_remap is True
    assert src._abi_version < 1   # never adopted v0

    # New valid v1 appears; pending remap resolves.
    path.unlink()
    _write_v1_segment(path, generation=0x33333333)
    r2 = src.read()
    assert isinstance(r2, StreamInvalidated)
    assert src._pending_remap is False
    assert src._unsupported_abi is False
    assert src._abi_version == SHM_ABI_VERSION
    src.close()


def test_shm_unsupported_same_inode_stays_invalid(tmp_path):
    """If the inode does NOT change, unsupported state stays in effect
    — every read continues to return StreamInvalidated."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    path.unlink()
    _write_v1_segment(path, abi_version=SHM_ABI_VERSION + 1)
    r = src.read()
    assert isinstance(r, StreamInvalidated)
    assert src._unsupported_abi is True
    # Multiple additional reads without inode change must remain SI.
    for _ in range(5):
        r = src.read()
        assert isinstance(r, StreamInvalidated)
    assert src._unsupported_abi is True
    src.close()


def test_shm_unsupported_recovers_via_temporary_absence(tmp_path):
    """v1 → v2 → file absent temporarily → new valid v1 at same path."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    # Reject v2.
    path.unlink()
    _write_v1_segment(path, abi_version=SHM_ABI_VERSION + 1)
    src.read()
    assert src._unsupported_abi is True

    # File temporarily absent.
    path.unlink()
    src.read()   # detect replacement (OSError from stat), enter pending
    assert src._unsupported_abi is False, (
        "cleared once the rejected inode is gone — clean slate for the "
        "still-pending new inode"
    )

    # Valid v1 appears; pending resolves.
    _write_v1_segment(path, generation=0xCAFECAFE)
    src.read()
    assert src._pending_remap is False
    assert src._abi_version == SHM_ABI_VERSION
    assert src._prev_generation == 0xCAFECAFE
    src.close()


# ---------------------------------------------------------------------------
# BLOCKER 2: cross-call publication ordering + no future bars
# ---------------------------------------------------------------------------


_HOP = 480


def _make_cap_with_processors(*procs):
    """Build a real CanonicalAnalysisPipeline and splice in extra test
    processors.  Uses V2SpectrumEngine so no cavacore native lib is
    required."""
    src = MagicMock()
    src.running = True
    cap = CanonicalAnalysisPipeline(
        source=src,
        engine=V2SpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000),
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    cap._processors = (*cap._processors, *procs)
    return cap


class _ManualProcessor:
    """Test processor that emits a caller-provided list of
    ProcessorUpdates on each feed() invocation."""

    def __init__(self, processor_id: str) -> None:
        self.processor_id = processor_id
        self._queue: list[list[ProcessorUpdate]] = []

    def push(self, updates: list[ProcessorUpdate]) -> None:
        self._queue.append(updates)

    def feed(self, frame):  # noqa: ARG002 — matches processor protocol
        if not self._queue:
            return []
        return self._queue.pop(0)

    def flush(self):
        return []

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def _make_frame(sample_pos: int) -> object:
    from huesync.canonicalizer import AnalysisPcmFrame
    return AnalysisPcmFrame(
        samples=np.zeros((_HOP, 2), dtype=np.float32),
        sample_pos=sample_pos,
        epoch_id="ep-cross-call",
        source_id="t",
        over_range=False,
    )


def test_beat_at_earlier_interval_does_not_receive_future_spectrum_bars():
    """Spectrum at [20,30) with bars=[0.9] and Beat at [0,10) in the
    same feed(): the Beat publication must NOT carry the spectrum bars
    (they belong to a later interval).

    Would FAIL on ce6a0f3: ``_process_canonical_frame`` eagerly
    updated ``_last_bars`` before ``_publish_by_interval`` ran, so
    the Beat [0,10) publication inherited the spectrum bars from
    [20,30).
    """
    spec = _ManualProcessor("spec_probe")
    beat = _ManualProcessor("beat_probe")
    cap = _make_cap_with_processors(spec, beat)

    spec.push([
        ProcessorUpdate(
            processor_id="spec_probe",
            sample_start=20, sample_end=30, bars=[0.9],
        )
    ])
    beat.push([
        ProcessorUpdate(
            processor_id="beat_probe",
            sample_start=0, sample_end=10, onset=True, onset_strength=0.5,
        )
    ])

    recs = cap.feed(_make_frame(sample_pos=0))
    by_key = {(r.sample_pos, r.sample_end): r for r in recs}
    assert (0, 10) in by_key, f"expected beat [0,10) record; got {list(by_key)}"
    assert (20, 30) in by_key, f"expected spec [20,30) record; got {list(by_key)}"
    beat_rec = by_key[(0, 10)]
    spec_rec = by_key[(20, 30)]
    assert beat_rec.features.bars == [], (
        "Beat at [0,10) must not carry Spectrum bars from the future "
        f"[20,30); got bars={beat_rec.features.bars}"
    )
    assert spec_rec.features.bars == [0.9]


def test_late_arriving_publication_is_dropped_not_reordered():
    """Reproduces the audit's CAVA + Beat cadence: after four feeds
    where cavacore emits [0,480)…[1440,1920), a fifth feed emits Beat
    at [0,480) and cavacore at [1920,2400).  The Beat record must be
    DROPPED (start=0 is far behind the transport frontier) rather
    than published as a backwards timestamp.
    """
    cavacore = _ManualProcessor("cavacore_probe")
    beat = _ManualProcessor("beat_probe")
    cap = _make_cap_with_processors(cavacore, beat)

    # Feeds 0..3: cavacore emits [i*480, (i+1)*480) each frame.
    for i in range(4):
        cavacore.push([
            ProcessorUpdate(
                processor_id="cavacore_probe",
                sample_start=i * 480, sample_end=(i + 1) * 480,
                bars=[float(i) * 0.1],
            )
        ])
        beat.push([])   # no beat this feed
        cap.feed(_make_frame(sample_pos=i * 480))

    # Feed 4: beat late-arrives at [0,480), cavacore moves to [1920,2400).
    cavacore.push([
        ProcessorUpdate(
            processor_id="cavacore_probe",
            sample_start=1920, sample_end=2400, bars=[0.4],
        )
    ])
    beat.push([
        ProcessorUpdate(
            processor_id="beat_probe",
            sample_start=0, sample_end=480, onset=True, onset_strength=0.5,
        )
    ])
    late_late_before = cap.pub_late_dropped_count
    recs = cap.feed(_make_frame(sample_pos=1920))

    starts = sorted(r.sample_pos for r in recs)
    # No published record may have sample_pos < 1920 (the frontier).
    assert all(s >= 1920 for s in starts), (
        f"backwards publication observed; starts={starts}"
    )
    assert cap.pub_late_dropped_count > late_late_before, (
        "the dropped late beat must be recorded in pub_late_dropped_count"
    )


def test_cross_call_publication_start_never_moves_backwards():
    """Randomised cadence: N feeds each emitting one Cavacore record
    and possibly a late Beat.  Published sample_pos must be
    monotonically non-decreasing across all feeds."""
    cavacore = _ManualProcessor("cavacore_probe")
    beat = _ManualProcessor("beat_probe")
    cap = _make_cap_with_processors(cavacore, beat)

    published: list[tuple[int, int]] = []
    for i in range(6):
        cavacore.push([
            ProcessorUpdate(
                processor_id="cavacore_probe",
                sample_start=i * 480, sample_end=(i + 1) * 480,
                bars=[0.1 * i],
            )
        ])
        if i >= 4:
            beat.push([
                ProcessorUpdate(
                    processor_id="beat_probe",
                    sample_start=(i - 4) * 480, sample_end=(i - 3) * 480,
                    onset=True, onset_strength=0.5,
                )
            ])
        else:
            beat.push([])
        for rec in cap.feed(_make_frame(sample_pos=i * 480)):
            published.append((rec.sample_pos, rec.sample_end))

    starts = [s for s, _ in published]
    assert starts == sorted(starts), (
        f"sample_pos must be monotonically non-decreasing; got {starts}"
    )


def test_pending_state_is_bounded_across_many_feeds():
    """``_pending_onset`` and other retained state must not grow
    without bound across many feed() calls."""
    beat = _ManualProcessor("beat_probe")
    cap = _make_cap_with_processors(beat)
    for i in range(50):
        beat.push([
            ProcessorUpdate(
                processor_id="beat_probe",
                sample_start=i * _HOP, sample_end=(i + 1) * _HOP,
                onset=True, onset_strength=0.5,
            )
        ])
        cap.feed(_make_frame(sample_pos=i * _HOP))
    # _pending_onset is cleared inside _process_canonical_frame; the
    # invariant is that it never accumulates.
    assert len(cap._pending_onset) <= 8, (
        f"_pending_onset grew unbounded: len={len(cap._pending_onset)}"
    )


def test_eos_with_delayed_processor_preserves_ordering():
    """A delayed Beat that only emits during flush must not produce
    records with sample_pos < the transport frontier."""
    beat = _ManualProcessor("beat_probe")
    cap = _make_cap_with_processors(beat)

    # Advance the transport frontier.
    for i in range(3):
        beat.push([
            ProcessorUpdate(
                processor_id="beat_probe",
                sample_start=i * _HOP, sample_end=(i + 1) * _HOP,
                onset=False, onset_strength=0.0,
            )
        ])
        cap.feed(_make_frame(sample_pos=i * _HOP))

    frontier = cap._last_pub_start
    # Simulate an EOS whose flush emits an OLD interval — must be
    # dropped rather than published backwards.
    late_before = cap.pub_late_dropped_count
    # Force a manual flush call with an out-of-order update.
    # We do this by injecting through _publish_by_interval directly,
    # matching the composition helper used by _flush_engine.
    late_update = ProcessorUpdate(
        processor_id="beat_probe",
        sample_start=0, sample_end=_HOP,
        onset=True, onset_strength=0.5,
    )
    records = cap._publish_by_interval(
        epoch_id="ep-cross-call",
        spectrum_updates=[],
        other_updates=[late_update],
        clamp_end=None,
    )
    assert records == [], "late EOS update must be dropped, not emitted"
    assert cap.pub_late_dropped_count > late_before
    # Frontier must not have regressed.
    assert cap._last_pub_start == frontier


# ---------------------------------------------------------------------------
# BLOCKER 1: retiring worker ownership
# ---------------------------------------------------------------------------


class _BlockingSource:
    """PcmSource stub that blocks in read() until released.

    Tracks the maximum number of threads observed inside read() at any
    single moment so the "MAX SIMULTANEOUS SOURCE READERS = 1" audit
    invariant can be asserted directly.
    """

    def __init__(self) -> None:
        self._release = threading.Event()
        self._in_read_lock = threading.Lock()
        self._in_read = 0
        self.max_concurrent = 0
        self.total_reads = 0
        self.running = True

    def read(self):  # matches SqueezeliteShmStereoSource.read protocol
        with self._in_read_lock:
            self._in_read += 1
            if self._in_read > self.max_concurrent:
                self.max_concurrent = self._in_read
            self.total_reads += 1
        try:
            self._release.wait(timeout=5.0)
            return TemporarilyNoData()
        finally:
            with self._in_read_lock:
                self._in_read -= 1

    def close(self) -> None:
        self._release.set()

    def release(self) -> None:
        self._release.set()


def _make_engine_and_start(source):
    """Build a real SyncEngine whose analyser is a CanonicalAnalysisPipeline
    on the given blocking source, and start it so the worker enters
    ``source.read()``."""
    cap = CanonicalAnalysisPipeline(
        source=source,
        engine=V2SpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000),
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    with patch("huesync.sync_engine.CavaPipeline"):
        engine = SyncEngine(
            fifo_path=None, profile=Profile(name="test"), analyser=cap
        )
    cap.start()
    # Wait until the worker has entered source.read().
    deadline = time.monotonic() + 2.0
    while source.total_reads == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert source.total_reads > 0, "worker never entered source.read()"
    return engine, cap


def test_timeout_never_creates_second_concurrent_reader():
    """Round-3 blocker: on old-stop timeout, no rebuilt analyser may
    be started against the same source.  ``max_concurrent`` must stay
    at 1 throughout.
    """
    source = _BlockingSource()
    engine, cap = _make_engine_and_start(source)
    try:
        candidate = MagicMock()

        def rebuild_old():
            # If reached, we would spawn a 2nd reader — the test would
            # then observe max_concurrent >= 2.  Ce6a0f3's timeout
            # branch actively invoked this path.
            rebuilt = CanonicalAnalysisPipeline(
                source=source,
                engine=V2SpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000),
                onset_method="combined",
                onset_delta=0.1, onset_alpha=0.9,
                superflux_mu=3, superflux_lag=2,
                bass_hz=250, mid_hz=2000,
            )
            return rebuilt

        with pytest.raises(RuntimeError):
            engine.replace_analyser(candidate, rebuild_old=rebuild_old)
        # Candidate closed exactly once.
        candidate.stop.assert_called_once()
        # Old worker is still blocked in source.read(); its stop()
        # timed out.  Give a moment for any stray scheduling.
        time.sleep(0.1)
        assert source.max_concurrent == 1, (
            f"round-3 audit violation: max concurrent readers = "
            f"{source.max_concurrent}"
        )
        # Old is tracked in _retiring.
        assert cap in engine._retiring
        # self._analyser stays pointing at old (retiring).
        assert engine._analyser is cap
    finally:
        source.release()
        engine.stop()


def test_shutdown_joins_retiring_worker():
    """SyncEngine.stop() must join both the current analyser and any
    workers left in _retiring."""
    source = _BlockingSource()
    engine, cap = _make_engine_and_start(source)
    try:
        candidate = MagicMock()
        with pytest.raises(RuntimeError):
            engine.replace_analyser(candidate)
        assert cap in engine._retiring
    finally:
        source.release()
        # stop() must wait for the (previously blocked) worker to exit.
        engine.stop()
        deadline = time.monotonic() + 3.0
        while cap._thread is not None and cap._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cap._thread is None or not cap._thread.is_alive(), (
            "SyncEngine.stop() must join the retiring worker"
        )
        # _retiring is cleared after stop().
        assert engine._retiring == []


def test_subsequent_replace_refused_while_retirement_pending():
    """While retirement is in progress, replace_analyser() must
    refuse to start a new reader — that would violate the "at most
    one reader on the source" invariant."""
    source = _BlockingSource()
    engine, cap = _make_engine_and_start(source)
    try:
        first_candidate = MagicMock()
        with pytest.raises(RuntimeError):
            engine.replace_analyser(first_candidate)
        assert cap in engine._retiring

        # Retry immediately — worker is still blocked, so retirement
        # is still pending.  Refuse and close the second candidate.
        second_candidate = MagicMock()
        with pytest.raises(RuntimeError, match="retiring|RETIRING"):
            engine.replace_analyser(second_candidate)
        second_candidate.stop.assert_called_once()
    finally:
        source.release()
        engine.stop()


def test_replace_after_retirement_completes_succeeds():
    """Once the retiring worker has actually exited, a subsequent
    replace_analyser() must succeed."""
    source = _BlockingSource()
    engine, cap = _make_engine_and_start(source)
    try:
        with pytest.raises(RuntimeError):
            engine.replace_analyser(MagicMock())
        assert cap in engine._retiring
        # Release the blocked worker so retirement can complete.
        source.release()
        # Wait for the worker to exit.
        deadline = time.monotonic() + 3.0
        while cap._thread is not None and cap._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not cap._thread.is_alive()

        # Now a fresh replacement should work.
        rebuilt = MagicMock()
        rebuilt.latest.return_value = None
        # The old.stop() is called first inside replace_analyser; since
        # the worker has exited, it returns True (clean stop).  Then
        # rebuilt.start() runs.
        engine.replace_analyser(rebuilt)
        rebuilt.start.assert_called_once()
        assert engine._analyser is rebuilt
        assert engine._retiring == []
    finally:
        source.release()
        engine.stop()


def test_retirement_pending_property_reflects_worker_state():
    """``SyncEngine.retirement_pending`` must return True while a
    retiring worker is alive and False once it has exited."""
    source = _BlockingSource()
    engine, cap = _make_engine_and_start(source)
    try:
        with pytest.raises(RuntimeError):
            engine.replace_analyser(MagicMock())
        assert engine.retirement_pending is True

        source.release()
        deadline = time.monotonic() + 3.0
        while cap._thread is not None and cap._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        # Once the thread has exited, the property must clear.
        assert engine.retirement_pending is False
    finally:
        source.release()
        engine.stop()
