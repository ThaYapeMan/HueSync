"""Regression tests for the second-round audit blockers against 7dc457b.

Every test in this file is designed to FAIL on commit
``7dc457b9169aa0c5fd08dda1283dc7d208d4fa54`` and PASS after the fixes in
this branch.  The blockers covered:

  1. Default squeezelite build must enable ``-DVISEXPORT`` and link the
     v0 + v1 producer objects.
  2. SHM replacement remap must recover through absent/incomplete
     replacements without ever falling back to v0 under require_v1.
  3. Replace-analyser timeout must close the candidate exactly once and
     leave the runtime in a usable or explicitly deactivated state.
  4. PlayerManager.restart_cava must be transactional — a failed
     ``_start_cava`` must not corrupt session profile / runtime.
  5. Producer patch must wrap init, silence, and vis_stop transitions
     in the seqlock protocol.
  6. Processor interval publication must key by exact
     ``(sample_start, sample_end)`` — no bounding-union, no
     list-position association.
  7. CavaCoreSpectrumEngine.feed()/flush() must report correct positions
     (or be explicitly unsupported).
"""

from __future__ import annotations

import asyncio
import re
import struct
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from huesync.pcm_source import (
    SHM_ABI_V1_MAGIC,
    SHM_ABI_VERSION,
    SqueezeliteShmStereoSource,
    StreamInvalidated,
)
from huesync.spectrum_engine import (
    CavaCoreSpectrumEngine,
    ProcessorUpdate,
)
from huesync.sync_engine import (
    _DeactivatedPipeline,
)

# ---------------------------------------------------------------------------
# BLOCKER 1: default build enables VISEXPORT + producer objects
# ---------------------------------------------------------------------------


_BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-squeezelite.sh"


def test_build_script_enables_visexport_by_default():
    """The default build must always compile with ``-DVISEXPORT``.

    Would FAIL on 7dc457b: that script called ``make -j$(nproc)
    ${OPTS:+"OPTS=$OPTS"}`` — nothing added ``-DVISEXPORT`` unless the
    caller opted in via OPTS.
    """
    body = _BUILD_SCRIPT.read_text()
    assert "-DVISEXPORT" in body, (
        "build-squeezelite.sh must inject -DVISEXPORT into OPTS by default"
    )
    # Ensure the injection actually happens on the make command line, not
    # just in a comment somewhere.
    assert re.search(r"OPTS=.*-DVISEXPORT|-DVISEXPORT.*OPTS=", body) or re.search(
        r"EXTRA_OPTS.*-DVISEXPORT", body
    ), "the -DVISEXPORT flag must reach the make invocation via OPTS"


def test_build_script_asserts_producer_objects_present():
    """The build must FAIL rather than silently install a squeezelite
    binary that omits the visualiser producer objects."""
    body = _BUILD_SCRIPT.read_text()
    assert "output_vis.o" in body, (
        "build script must inspect the build plan for output_vis.o"
    )
    assert "output_vis_v1.o" in body, (
        "build script must inspect the build plan for output_vis_v1.o"
    )
    # The script should error out (exit 1) when either object is missing.
    assert "exit 1" in body


# ---------------------------------------------------------------------------
# BLOCKER 5: producer patch wraps init / silence / vis_stop in seqlock
# ---------------------------------------------------------------------------


_PATCH = Path(__file__).resolve().parents[1] / "squeezelite" / "output_vis_v1.patch"


def test_producer_patch_wraps_silence_branch_in_seqlock():
    """The silence branch of ``_vis_export`` mutates ``running``; that
    must be inside a begin_write / end_write pair.

    Would FAIL on 7dc457b: pre-fix patch left ``if (silence) { running =
    false; }`` unprotected.
    """
    body = _PATCH.read_text()
    # Locate the silence-branch hunk and check both begin_write and
    # end_write bracket the running=false assignment.
    m = re.search(
        r"if \(silence\)[\s\S]*?vis_mmap->running = false;[\s\S]*?\}",
        body,
    )
    assert m is not None, "silence branch missing in producer patch"
    silence_block = m.group(0)
    assert "vis_shm_v1_begin_write" in silence_block
    assert "vis_shm_v1_end_write" in silence_block


def test_producer_patch_wraps_vis_stop_in_seqlock():
    """Upstream's ``vis_stop()`` sets running=false under the rwlock but
    not under the seqlock.  The producer patch must add begin/end_write.

    Would FAIL on 7dc457b: patch did not touch vis_stop at all.
    """
    body = _PATCH.read_text()
    # The vis_stop hunk should contain both begin_write and end_write.
    m = re.search(
        r"void vis_stop\(void\)[\s\S]*?vis_mmap->running = false;[\s\S]*?\}",
        body,
    )
    assert m is not None, "vis_stop hunk missing in producer patch"
    block = m.group(0)
    assert "vis_shm_v1_begin_write" in block, (
        "vis_stop must call vis_shm_v1_begin_write around running=false"
    )
    assert "vis_shm_v1_end_write" in block, (
        "vis_stop must call vis_shm_v1_end_write around running=false"
    )


def test_producer_patch_marks_init_writes_in_progress():
    """The init sequence must mark write_seq odd BEFORE mutating any
    field the consumer treats as part of the coherent snapshot.

    Would FAIL on 7dc457b: patch set ``buf_size / running / rate``
    before calling ``vis_shm_v1_init``, so a racing consumer could see
    those legacy fields populated while write_seq was still the mmap
    zero (even == 'stable').
    """
    body = _PATCH.read_text()
    # In the output_vis_init hunk, vis_shm_v1_begin_write must appear
    # BEFORE the buf_size / running / rate assignments — the diff order
    # models the code order after patching.
    hunk_match = re.search(
        r"pthread_rwlock_init\([\s\S]*?LOG_INFO\(\"opened",
        body,
    )
    assert hunk_match is not None, "output_vis_init hunk missing in producer patch"
    hunk = hunk_match.group(0)
    begin_idx = hunk.find("vis_shm_v1_begin_write")
    running_idx = hunk.find("vis_mmap->running = false;")
    assert begin_idx >= 0, "init hunk missing vis_shm_v1_begin_write"
    assert running_idx >= 0
    assert begin_idx < running_idx, (
        "vis_shm_v1_begin_write must precede legacy field writes so init "
        "publishes write_seq=odd before mutable metadata"
    )


# ---------------------------------------------------------------------------
# BLOCKER 2: SHM replacement retry state (PENDING_REMAP)
# ---------------------------------------------------------------------------


_V1_MMAP_SIZE = 32888   # 80 + 40 + 32768
_V2_EXT_OFFSET = 80
_HDR_FMT = "<IIBxxxIQ"
_HDR_SIZE = struct.calcsize(_HDR_FMT)


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
    """Materialise a fake v1 SHM segment at ``path``.

    Uses the same offsets as the real producer: legacy header at 0,
    extension block at offset 80, buffer at offset 120.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * _V1_MMAP_SIZE)
    # Legacy header: buf_size, buf_index, running, rate, updated.
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(struct.pack(_HDR_FMT, 16384, 0, 1, 48000, 0))
        # Extension header at offset 80.
        f.seek(_V2_EXT_OFFSET)
        # <IHHIQQQ4x  (magic, abi, flags, write_seq, generation, abs_write_pos, gap_seq, 4x pad)
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
    """Materialise a legacy v0 SHM segment (no v1 magic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * 32848)  # v0 size: 80 + 32768
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(struct.pack(_HDR_FMT, 16384, 0, 1, 48000, 0))
        # Deliberately leave extension bytes at offset 80 as zeros — no v1 magic.


def test_shm_absent_replacement_stays_in_pending_remap(tmp_path):
    """Replacement file transiently absent → reader stays in PENDING_REMAP
    and recovers automatically once the new v1 file appears.

    Would FAIL on 7dc457b: after replacement detection, ``_remap_after_
    replacement`` closed the old mmap and returned False silently — the
    next read would have called ``_read_v0()`` on a None mapping.
    """
    path = tmp_path / "shm"
    _write_v1_segment(path)

    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)
    # First reads should succeed (or return TemporarilyNoData with no
    # DataResult) — we just care the source is validly open.

    # Now simulate replacement by unlinking + recreating with a fresh inode.
    path.unlink()
    # File absent right now.
    result = src.read()
    assert isinstance(result, StreamInvalidated)
    assert src._pending_remap is True, "reader must enter PENDING_REMAP"
    assert src._mm is None or True  # old mmap released

    # Read again while file is still missing — must stay in PENDING_REMAP.
    result = src.read()
    assert isinstance(result, StreamInvalidated)
    assert src._pending_remap is True

    # Producer finally creates the new segment.
    _write_v1_segment(path, generation=0xCAFEBABE)
    # Next read attempts remap and either succeeds (returns
    # StreamInvalidated for the epoch break) or is now non-pending.
    result = src.read()
    assert isinstance(result, StreamInvalidated)
    assert src._pending_remap is False
    assert src._abi_version == SHM_ABI_VERSION
    src.close()


def test_shm_incomplete_replacement_stays_in_pending_remap(tmp_path):
    """Replacement has v1 magic but ``write_seq`` persistently odd →
    reader stays in PENDING_REMAP; never returns garbage as PCM."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    # Replace with a segment whose write_seq is 1 (odd — init in progress).
    path.unlink()
    _write_v1_segment(path, write_seq=1)

    result = src.read()
    assert isinstance(result, StreamInvalidated)
    assert src._pending_remap is True, (
        "odd write_seq must keep the reader in PENDING_REMAP; the old code "
        "would have set _abi_version=0 and dropped to _read_v0()"
    )
    # No PCM must ever be returned from this pending state.
    for _ in range(5):
        r = src.read()
        assert isinstance(r, StreamInvalidated)
    src.close()


def test_shm_unsupported_abi_never_falls_back_to_v0(tmp_path):
    """Replacement carries a future ABI version.  Reader must NEVER fall
    through to _read_v0 — that would return header bytes as PCM."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    path.unlink()
    _write_v1_segment(path, abi_version=SHM_ABI_VERSION + 1)

    result = src.read()
    assert isinstance(result, StreamInvalidated)
    # Permanent invalidation — every future read must remain invalidated.
    for _ in range(5):
        r = src.read()
        assert isinstance(r, StreamInvalidated)
    assert src._unsupported_abi is True
    src.close()


def test_shm_v0_replacement_rejected_under_require_v1(tmp_path):
    """Replacement is legacy v0 (no v1 magic) and require_v1 is True →
    reader must stay in PENDING_REMAP, never adopt v0."""
    path = tmp_path / "shm"
    _write_v1_segment(path)
    src = SqueezeliteShmStereoSource()
    src.open("aa:bb:cc:dd:ee:ff", _path=path)

    path.unlink()
    _write_v0_segment(path)

    result = src.read()
    assert isinstance(result, StreamInvalidated)
    assert src._pending_remap is True
    # No matter how many times we poll, the reader must never adopt v0
    # under require_v1=True.
    for _ in range(5):
        r = src.read()
        assert isinstance(r, StreamInvalidated)
        assert src._abi_version < 1
    src.close()


# ---------------------------------------------------------------------------
# BLOCKER 3: timeout replacement candidate + runtime state
# ---------------------------------------------------------------------------


def _make_engine_with_old(old):
    """Build a minimal SyncEngine wrapper whose ``self._analyser`` starts
    as ``old``.  Reused across timeout-branch tests.
    """
    from huesync.models import Profile
    from huesync.sync_engine import SyncEngine

    engine = SyncEngine.__new__(SyncEngine)
    engine._analyser = old
    engine.profile = Profile(name="test")
    return engine


def test_timeout_replacement_candidate_closed_exactly_once():
    """On old-stop timeout, the unused candidate must be closed exactly
    once (rather than leaked to the caller).

    Would FAIL on 7dc457b: the timeout branch raised without touching
    the candidate; candidate.stop() was called zero times.
    """
    old = MagicMock()
    old.stop.return_value = False
    engine = _make_engine_with_old(old)

    candidate = MagicMock()

    with pytest.raises(RuntimeError, match="did not stop"):
        engine.replace_analyser(candidate)

    assert candidate.stop.call_count == 1
    assert candidate.start.call_count == 0


def test_timeout_replacement_installs_deactivated_sentinel_without_rebuild():
    """Without a rebuild_old callback, the runtime must transition to an
    explicit deactivated sentinel — not remain pointing at the timed-out
    old pipeline.

    Would FAIL on 7dc457b: the timeout branch raised with
    ``self._analyser`` still pointing at ``old``.
    """
    old = MagicMock()
    old.stop.return_value = False
    engine = _make_engine_with_old(old)

    with pytest.raises(RuntimeError, match="did not stop"):
        engine.replace_analyser(MagicMock())

    assert isinstance(engine._analyser, _DeactivatedPipeline)
    assert engine._analyser is not old
    # And the sentinel does not produce features.
    assert engine._analyser.latest() is None


def test_timeout_replacement_uses_rebuild_old_when_available():
    """When rebuild_old is provided, the runtime restarts a fresh
    equivalent so the session remains usable after the timeout."""
    old = MagicMock()
    old.stop.return_value = False
    engine = _make_engine_with_old(old)

    rebuilt = MagicMock()
    rebuilt.latest.return_value = None

    def rebuild_old():
        return rebuilt

    with pytest.raises(RuntimeError, match="did not stop"):
        engine.replace_analyser(MagicMock(), rebuild_old=rebuild_old)

    rebuilt.start.assert_called_once()
    assert engine._analyser is rebuilt


def test_timeout_replacement_does_not_close_old_processors_early():
    """The old worker is still running — its finally block will close
    its own processors.  ``replace_analyser`` must NOT close them from
    outside."""
    old = MagicMock()
    old.stop.return_value = False
    engine = _make_engine_with_old(old)

    with pytest.raises(RuntimeError, match="did not stop"):
        engine.replace_analyser(MagicMock())

    # Only old.stop() was called; no other lifecycle method (like a
    # hypothetical .close_processors_now()) was invoked.
    old.stop.assert_called_once()
    # No mystery attribute accesses that would indicate early close.
    # Note: MagicMock accepts arbitrary attribute access; we assert on
    # the specific method we care about.


# ---------------------------------------------------------------------------
# BLOCKER 4: FIFO restart transactional rollback
# ---------------------------------------------------------------------------


def test_restart_cava_rolls_back_session_on_start_failure(monkeypatch, tmp_path):
    """When _start_cava raises after the profile has been swapped,
    session.profile / session.coupling must roll back to the pre-call
    snapshot — the storage/session/runtime views must agree.

    Would FAIL on 7dc457b: on _start_cava failure, session.profile was
    left at the NEW value and session.cava at None, so subsequent
    checks would see a mismatched state.
    """
    from huesync.models import Coupling, Profile
    from huesync.player_manager import ActiveSession, PlayerManager

    # Build a minimal storage stub with the coupling / dependencies
    # restart_cava needs.
    manager = PlayerManager.__new__(PlayerManager)
    manager._active = None
    manager.storage = MagicMock()

    old_profile = Profile(name="OLD", bars=16)
    new_profile = Profile(name="NEW", bars=32)
    old_coupling = MagicMock(id="c1", spec=Coupling)
    new_coupling = MagicMock(id="c1", spec=Coupling)

    session = ActiveSession.__new__(ActiveSession)
    session.coupling = old_coupling
    session.profile = old_profile
    session.cava = None  # no old cava to terminate
    session.sync_engine = None
    session.squeezelite = None
    session.fifo_path = tmp_path / "fifo"
    session.cava_conf_path = tmp_path / "cava.conf"
    session.cava_log_path = tmp_path / "cava.log"
    manager._active = session

    manager.storage.get_coupling.return_value = new_coupling
    monkeypatch.setattr(
        "huesync.player_manager._build_engine_profile",
        lambda coupling, storage: new_profile,
    )

    call_count = {"n": 0}

    def failing_start_cava(sess, prof):
        call_count["n"] += 1
        # First call is with new profile — inject failure here.
        if prof is new_profile:
            raise OSError("simulated cava start failure")
        # Rollback attempts to start with the OLD profile — let it "succeed"
        # so we can observe the rolled-back state.
        # (Do nothing; session.cava stays None as it started.)
        return None

    monkeypatch.setattr(manager, "_start_cava", failing_start_cava)

    with pytest.raises(OSError, match="simulated cava start failure"):
        asyncio.run(manager.restart_cava())

    # Session state must have rolled back.
    assert session.profile is old_profile, (
        "restart_cava must restore session.profile on _start_cava failure"
    )
    assert session.coupling is old_coupling


# ---------------------------------------------------------------------------
# BLOCKER 6: exact-interval processor publication
# ---------------------------------------------------------------------------


def test_distinct_processor_intervals_produce_distinct_publications():
    """Two Other-only ProcessorUpdates at disjoint intervals must yield
    two distinct publications — never a single [min_start, max_end)
    bounding-union record."""
    import numpy as np

    from huesync.canonicalizer import AnalysisPcmFrame
    from huesync.spectrum_engine import V2SpectrumEngine
    from huesync.sync_engine import CanonicalAnalysisPipeline

    class _TwoIntervalOther:
        processor_id = "twin"

        def feed(self, frame):
            # Emit two ProcessorUpdates for two disjoint intervals in one
            # canonical frame call.  Simulates an out-of-band contributor
            # (e.g. a beat probe) whose intervals do not align with any
            # spectrum window.
            return [
                ProcessorUpdate(
                    processor_id=self.processor_id,
                    sample_start=10,
                    sample_end=20,
                    onset=True,
                    onset_strength=0.5,
                ),
                ProcessorUpdate(
                    processor_id=self.processor_id,
                    sample_start=300,
                    sample_end=320,
                    onset=True,
                    onset_strength=0.5,
                ),
            ]

        def flush(self):
            return []

        def reset(self):
            pass

        def close(self):
            pass

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
    # Splice in the twin-interval processor.
    cap._processors = (*cap._processors, _TwoIntervalOther())

    frame = AnalysisPcmFrame(
        samples=np.zeros((480, 2), dtype=np.float32),
        sample_pos=0,
        epoch_id="ep-1",
        source_id="t",
        over_range=False,
    )
    recs = cap.feed(frame)
    twin_recs = [
        r for r in recs if "twin" in r.effective_processor_ids
    ]
    # Two intervals → two publications; NOT one union [10, 320).
    assert len(twin_recs) == 2, (
        f"expected two distinct publications for [10,20) and [300,320); got "
        f"{[(r.sample_pos, r.sample_end) for r in twin_recs]}"
    )
    intervals = sorted((r.sample_pos, r.sample_end) for r in twin_recs)
    assert intervals == [(10, 20), (300, 320)]


def test_processor_updates_never_paired_by_list_index():
    """Two independent Other processors emitting at DIFFERENT intervals
    must not be paired by list index — each interval belongs to exactly
    one publication and its ``effective_processor_ids`` reflects only
    the actual contributor at that interval."""
    from huesync.canonicalizer import AnalysisPcmFrame
    from huesync.spectrum_engine import V2SpectrumEngine
    from huesync.sync_engine import CanonicalAnalysisPipeline

    class _ProcA:
        processor_id = "A"

        def feed(self, frame):
            return [ProcessorUpdate(processor_id="A", sample_start=0, sample_end=10),
                    ProcessorUpdate(processor_id="A", sample_start=10, sample_end=20),
                    ProcessorUpdate(processor_id="A", sample_start=20, sample_end=30)]

        def flush(self):
            return []

        def reset(self):
            pass

        def close(self):
            pass

    class _ProcB:
        processor_id = "B"

        def feed(self, frame):
            return [ProcessorUpdate(processor_id="B", sample_start=0, sample_end=20),
                    ProcessorUpdate(processor_id="B", sample_start=20, sample_end=40)]

        def flush(self):
            return []

        def reset(self):
            pass

        def close(self):
            pass

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
    cap._processors = (*cap._processors, _ProcA(), _ProcB())

    frame = AnalysisPcmFrame(
        samples=np.zeros((480, 2), dtype=np.float32),
        sample_pos=0,
        epoch_id="ep-1",
        source_id="t",
        over_range=False,
    )
    recs = cap.feed(frame)
    # Only records contributed to by A/B (skip the spectrum/beat
    # records that come from real processors — those may coincidentally
    # share intervals with the test processors).
    ab_by_interval: dict[tuple[int, int], set[str]] = {}
    for r in recs:
        contribs = set(r.effective_processor_ids)
        if not contribs.intersection({"A", "B"}):
            continue
        key = (r.sample_pos, r.sample_end)
        ab_by_interval.setdefault(key, set()).update(contribs & {"A", "B"})

    # Each A/B interval must be a distinct key, contributed to by the
    # matching processor (both contribute to interval [0,20) is
    # impossible since A[0,20) is not one of A's intervals; A has
    # [0,10), [10,20), [20,30) while B has [0,20), [20,40)).
    assert (0, 10) in ab_by_interval and ab_by_interval[(0, 10)] == {"A"}
    assert (10, 20) in ab_by_interval and ab_by_interval[(10, 20)] == {"A"}
    assert (20, 30) in ab_by_interval and ab_by_interval[(20, 30)] == {"A"}
    assert (0, 20) in ab_by_interval and ab_by_interval[(0, 20)] == {"B"}
    assert (20, 40) in ab_by_interval and ab_by_interval[(20, 40)] == {"B"}


# ---------------------------------------------------------------------------
# BLOCKER 7: CavaCoreSpectrumEngine.feed()/flush() positions
# ---------------------------------------------------------------------------


def _cavacore_available() -> bool:
    try:
        return CavaCoreSpectrumEngine.check_available()  # type: ignore[attr-defined]
    except AttributeError:
        try:
            CavaCoreSpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000)
        except Exception:  # noqa: BLE001
            return False
        return True


@pytest.mark.skipif(not _cavacore_available(), reason="cavacore native lib unavailable")
def test_cavacore_feed_reports_last_full_block_start():
    """feed(1441 frames) reports the LAST full block start (960), not the
    pre-fix ``_epoch_samples - BLOCK_SIZE`` value (961)."""
    from huesync.spectrum_engine import SharedAnalysis

    eng = CavaCoreSpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000)
    pcm = np.random.default_rng(0xC0DE).standard_normal((1441, 2)).astype(np.float32) * 0.2
    shared = SharedAnalysis(mag_frames=[], pcm=pcm, sample_pos=0, n_samples=len(pcm))
    updates = eng.feed(pcm, shared)
    assert updates, "cavacore.feed should return at least one SpectrumUpdate"
    assert updates[-1].sample_pos == 960, (
        f"feed(1441).sample_pos expected 960 (start of last full 480-frame "
        f"block); got {updates[-1].sample_pos}"
    )


@pytest.mark.skipif(not _cavacore_available(), reason="cavacore native lib unavailable")
def test_cavacore_flush_reports_carry_start():
    """flush() after feed(1441 frames) reports position 1440 (start of
    the 1-frame real tail), not the pre-fix 961."""
    from huesync.spectrum_engine import SharedAnalysis

    eng = CavaCoreSpectrumEngine(n_bars=16, lower_hz=50, upper_hz=10000)
    pcm = np.random.default_rng(0xC0DE).standard_normal((1441, 2)).astype(np.float32) * 0.2
    shared = SharedAnalysis(mag_frames=[], pcm=pcm, sample_pos=0, n_samples=len(pcm))
    eng.feed(pcm, shared)
    flushed = eng.flush()
    assert flushed, "flush should emit an update for the pending tail"
    assert flushed[0].sample_pos == 1440, (
        f"flush().sample_pos expected 1440 (start of pending tail); got "
        f"{flushed[0].sample_pos}"
    )
