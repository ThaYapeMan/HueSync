"""Lifecycle regression tests for CanonicalAnalysisPipeline and source adapters.

All critical tests exercise either:
- the actual _run() production worker (via threading), or
- _process_canonical_frame() — the exact helper _run() calls.

NOT the monkey-patched _run_one_canonical test dispatcher from test_phase3.py.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from pipeline_factory import make_pipeline

from huesync.canonicalizer import (
    AudioCanonicalizer,
    CanonicalData,
    DataResult,
    DecodedSourceFrame,
    EndOfStream,
    InvalidationCause,
    StreamInvalidated,
    TemporarilyNoData,
)
from huesync.pcm_source import (
    _BUF_OFFSET,
    _HDR_FMT,
    _HDR_OFFSET,
    _MMAP_SIZE,
    AIRPLAY_SAMPLE_RATE,
    VIS_BUF_SIZE,
    AirPlayPipeStereoSource,
    SqueezeliteShmStereoSource,
)
from huesync.sync_engine import _CAP_POLL_S, CanonicalAnalysisPipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_RATE = 48000


def _make_pipeline(**kwargs) -> CanonicalAnalysisPipeline:
    src = MagicMock()
    src.running = True
    defaults = dict(
        source=src,
        bars=30,
        lower_cutoff_freq=50,
        higher_cutoff_freq=10000,
        onset_method="combined",
        onset_delta=0.1,
        onset_alpha=0.9,
        superflux_mu=3,
        superflux_lag=2,
        bass_hz=250,
        mid_hz=2000,
    )
    defaults.update(kwargs)
    return make_pipeline(**defaults)


def _sine_stereo(freq: float = 440.0, n: int = _CANONICAL_RATE) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / _CANONICAL_RATE
    sig = (np.sin(2 * np.pi * freq * t) * 0.5).astype(np.float32)
    return np.column_stack([sig, sig])


def _make_canonical_frame(samples: np.ndarray, epoch_id: str = "ep-1") -> object:
    from huesync.canonicalizer import AnalysisPcmFrame
    frame = AnalysisPcmFrame(
        samples=samples,
        sample_pos=0,
        epoch_id=epoch_id,
        source_id="test:src",
        over_range=False,
    )
    return frame


def _feed_frames(pipeline: CanonicalAnalysisPipeline, stereo: np.ndarray,
                 epoch_id: str = "ep-1", chunk: int = 4096) -> None:
    offset = 0
    while offset < len(stereo):
        end = min(offset + chunk, len(stereo))
        frame = _make_canonical_frame(stereo[offset:end], epoch_id=epoch_id)
        pipeline.feed(frame)
        offset = end


def _make_decoded_frame(
    n_samples: int = 4410, rate: int = AIRPLAY_SAMPLE_RATE
) -> DecodedSourceFrame:
    samples = np.zeros((n_samples, 2), dtype=np.float32)
    return DecodedSourceFrame(
        samples=samples,
        sample_rate=rate,
        channels=2,
        source_id="test:src",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )


class _SeqSource:
    """Mock source that consumes a predetermined sequence, then blocks."""

    def __init__(self, sequence: list, done_event: threading.Event) -> None:
        self._seq = list(sequence)
        self._idx = 0
        self._done = done_event
        self._lock = threading.Lock()

    def read(self):
        with self._lock:
            if self._idx < len(self._seq):
                result = self._seq[self._idx]
                self._idx += 1
                if self._idx == len(self._seq):
                    self._done.set()
                return result
        # Sequence exhausted: block until stop is called
        time.sleep(0.2)
        return TemporarilyNoData()

    @property
    def running(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Defect 1a — StreamInvalidated in _run() must clear _latest
# ---------------------------------------------------------------------------


def test_invalidation_clears_latest_via_run():
    """StreamInvalidated received by the real _run() worker must clear _latest.

    Before fix: _run() called _reset_dsp() but left _latest non-None.
    After fix: _run() also clears _latest under the lock.
    """
    done = threading.Event()
    # Sequence: enough data to warm up DSP, then StreamInvalidated
    seq = [StreamInvalidated(cause=InvalidationCause.RECONNECT)]
    src = _SeqSource(seq, done)
    p = _make_pipeline(source=src)

    # Manually set _latest to a non-None value on the inner CAP (simulates prior audio).
    from huesync.types import AudioFeatures
    stale = AudioFeatures(
        bars=[0.5] * 30, bass=0.5, mid=0.5, full=0.5,
        centroid=0.5, sustained_energy=None,
    )
    # Plant a synthetic PublicationRecord so p.latest() returns non-None
    # before the invalidation lands.
    from huesync.spectrum_engine import PublicationRecord
    with p._pub_lock:
        p._latest_pub = PublicationRecord(
            sequence=0, epoch="stale", sample_pos=0, sample_end=0,
            features=stale, effective_spectrum_backend="v2",
        )

    p.start()
    done.wait(timeout=2.0)
    time.sleep(0.02)  # Let _run() process the result
    p.stop()

    assert p.latest() is None, (
        "StreamInvalidated must clear _latest; stale features must not remain"
    )


# ---------------------------------------------------------------------------
# Defect 1b — epoch warmup must not expose previous epoch features
# ---------------------------------------------------------------------------


def test_epoch_warmup_clears_latest():
    """Epoch transition in _process_canonical_frame must clear _latest immediately.

    Old epoch features must not remain visible during the new epoch's STFT
    warmup window (before the first canonical frame produces STFT output).
    Exercises _process_canonical_frame directly — the exact helper _run() calls.
    """
    p = _make_pipeline()

    # Prime epoch A with signal
    sig = _sine_stereo(440, n=3 * _CANONICAL_RATE)
    _feed_frames(p, sig, epoch_id="epoch-A")
    assert p.latest() is not None, "Epoch A must produce features"

    # Feed one tiny frame with a new epoch_id — before any STFT output
    # A 10-sample frame will not produce any STFT frames (need 2048).
    tiny = np.zeros((10, 2), dtype=np.float32)
    frame_b = _make_canonical_frame(tiny, epoch_id="epoch-B")
    p.feed(frame_b)

    # _latest must be None immediately after epoch change, before warmup completes.
    assert p.latest() is None, (
        "After epoch transition, _latest must be cleared during STFT warmup; "
        "old epoch features must not remain visible"
    )


# ---------------------------------------------------------------------------
# Defect 2 — double reset prevention
# ---------------------------------------------------------------------------


def test_dsp_reset_exactly_once_after_invalidation():
    """After _run() resets DSP on StreamInvalidated (_current_epoch_id → None),
    the next epoch's _process_canonical_frame must not reset again.

    Verified by checking onset_pipeline identity via the inner CAP: only ONE new
    instance after a StreamInvalidated + epoch-A → epoch-B transition.
    """
    p = _make_pipeline()
    cap = p  # inner CanonicalAnalysisPipeline

    # Prime epoch A
    sig_a = _sine_stereo(440, n=2 * _CANONICAL_RATE)
    _feed_frames(p, sig_a, epoch_id="epoch-A")

    # Simulate what _run() does on StreamInvalidated: call _reset_dsp() directly.
    cap._reset_dsp()  # sets _current_epoch_id = None; replaces onset_pipeline
    onset_after_reset = id(cap._beat_detector._state.onset_pipeline)
    assert cap._current_epoch_id is None, "_reset_dsp must clear _current_epoch_id"

    # Now feed first frame of epoch B — _current_epoch_id is None so no second reset.
    tiny = np.zeros((10, 2), dtype=np.float32)
    frame_b = _make_canonical_frame(tiny, epoch_id="epoch-B")
    p.feed(frame_b)

    # onset_pipeline must NOT have been replaced again (no double reset).
    assert id(cap._beat_detector._state.onset_pipeline) == onset_after_reset, (
        "Double reset detected: epoch-B arrival after already-reset state "
        "must not replace onset_pipeline a second time"
    )
    assert cap._current_epoch_id == "epoch-B", "epoch_id must be updated to epoch-B"


# ---------------------------------------------------------------------------
# Defect 3 — TemporarilyNoData must not reset DSP
# ---------------------------------------------------------------------------


def test_temp_no_data_does_not_reset_dsp_via_run():
    """TemporarilyNoData through the real _run() must not touch DSP state."""
    done = threading.Event()
    seq = [TemporarilyNoData()]
    src = _SeqSource(seq, done)
    p = _make_pipeline(source=src)

    # Prime some DSP state first
    sig = _sine_stereo(440, n=2 * _CANONICAL_RATE)
    _feed_frames(p, sig, epoch_id="ep-1")
    cap = p  # inner CanonicalAnalysisPipeline
    onset_id_before = id(cap._beat_detector._state.onset_pipeline)
    epoch_before = cap._current_epoch_id

    p.start()
    done.wait(timeout=2.0)
    time.sleep(0.02)
    p.stop()

    # DSP must not have been reset by TemporarilyNoData
    assert id(cap._beat_detector._state.onset_pipeline) == onset_id_before, (
        "TemporarilyNoData must not replace onset_pipeline"
    )
    assert cap._current_epoch_id == epoch_before, "TemporarilyNoData must not clear epoch_id"


# ---------------------------------------------------------------------------
# Defect 4 — EOS clears partial-byte remainder in AirPlayPipeStereoSource
# ---------------------------------------------------------------------------


def test_eos_clears_airplay_remainder():
    """EOF in AirPlayPipeStereoSource.read() must discard _remainder.

    Before fix: _remainder retained partial bytes from the old stream.
    After fix: _remainder = b"" before EndOfStream is returned.
    """
    r_fd, w_fd = os.pipe()
    src = AirPlayPipeStereoSource.__new__(AirPlayPipeStereoSource)
    src._path = Path("/synthetic/airplay.pcm")
    src._fd = r_fd
    src._last_data_t = None
    src._source_id = "airplay:/synthetic/airplay.pcm"

    # Plant a stale partial byte (simulates a prior partial frame carry)
    src._remainder = b"\xAB"

    # Close write end → EOF
    os.close(w_fd)

    result = src.read()

    assert isinstance(result, EndOfStream), f"Expected EndOfStream, got {result}"
    assert src._remainder == b"", (
        f"_remainder must be cleared on EOF; got {src._remainder!r}"
    )

    src.close()


# ---------------------------------------------------------------------------
# Defect 5 — EOS busy-spin: sleep after EndOfStream in _run()
# ---------------------------------------------------------------------------


def test_eos_no_busy_spin():
    """After EndOfStream, _run() must not spin at CPU rate.

    Without fix: source.read() called as fast as the CPU allows.
    With fix: _stop.wait(_POLL_S) inserted → max ~200 reads/s.
    Check: fewer than 100 calls in 100 ms (threshold is 5× the ~20 expected).
    """
    call_count = 0
    call_lock = threading.Lock()

    class _EofSource:
        def read(self):
            nonlocal call_count
            with call_lock:
                call_count += 1
            return EndOfStream()

        @property
        def running(self) -> bool:
            return True

    p = _make_pipeline(source=_EofSource())
    p.start()
    time.sleep(0.1)
    p.stop()

    with call_lock:
        n = call_count
    assert n < 100, (
        f"Busy-spin detected: {n} source reads in 100 ms "
        f"(expected < 100 with _CAP_POLL_S={_CAP_POLL_S}s sleep)"
    )


# ---------------------------------------------------------------------------
# Defect 6 — SHM torn read must produce StreamInvalidated
# ---------------------------------------------------------------------------


def _write_shm_vis_t(path: Path, buf_index: int = 0,
                     buffer: bytes = b"\x00" * (VIS_BUF_SIZE * 2)) -> None:
    hdr = struct.pack(_HDR_FMT, VIS_BUF_SIZE, buf_index, 1, 44100, 0)
    data = bytearray(_MMAP_SIZE)
    data[_HDR_OFFSET: _HDR_OFFSET + len(hdr)] = hdr
    data[_BUF_OFFSET: _BUF_OFFSET + len(buffer)] = buffer
    path.write_bytes(bytes(data))


def test_shm_torn_read_returns_stream_invalidated(tmp_path: Path) -> None:
    """SqueezeliteShmStereoSource torn read must return StreamInvalidated.

    Before fix: returned TemporarilyNoData (silently hiding the audio gap).
    After fix: returns StreamInvalidated(cause=UNKNOWN) so the epoch resets.
    """
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 100, 200)
    p = tmp_path / "shm"
    _write_shm_vis_t(p, buf_index=2, buffer=bytes(buf))

    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=False)
    src._prev_index = 0

    # Intercept _read_header so 2nd call (post-copy seqlock check) advances buf_index.
    _real = SqueezeliteShmStereoSource._read_header
    _calls = [0]

    def _patched(self_inner):
        _calls[0] += 1
        r = _real(self_inner)
        if _calls[0] == 2:
            return (r[0], (r[1] + 2) % VIS_BUF_SIZE, r[2], r[3], r[4])
        return r

    src._read_header = lambda: _patched(src)  # type: ignore[method-assign]

    result = src.read()

    assert isinstance(result, StreamInvalidated), (
        f"Torn read must return StreamInvalidated; got {type(result).__name__}"
    )
    assert result.cause == InvalidationCause.UNKNOWN
    src.close()


# ---------------------------------------------------------------------------
# Defect 7 — Worker exception clears stale _latest
# ---------------------------------------------------------------------------


def test_worker_exception_clears_latest():
    """If _run() worker raises, _latest must be cleared (no stale features).

    Before fix: thread exits silently, _latest retains old AudioFeatures.
    After fix: try/except in _run() clears _latest before thread exits.
    """
    crash_event = threading.Event()

    class _CrashAfterFirst:
        def __init__(self):
            self._first = True

        def read(self):
            if self._first:
                self._first = False
                return TemporarilyNoData()
            crash_event.set()
            raise RuntimeError("Simulated worker crash")

        @property
        def running(self) -> bool:
            return True

    p = _make_pipeline(source=_CrashAfterFirst())

    # Plant stale features on the inner CAP
    from huesync.types import AudioFeatures
    stale = AudioFeatures(
        bars=[0.8] * 30, bass=0.8, mid=0.8, full=0.8,
        centroid=0.5, sustained_energy=None,
    )
    # Plant a synthetic PublicationRecord so p.latest() returns non-None
    # before the invalidation lands.
    from huesync.spectrum_engine import PublicationRecord
    with p._pub_lock:
        p._latest_pub = PublicationRecord(
            sequence=0, epoch="stale", sample_pos=0, sample_end=0,
            features=stale, effective_spectrum_backend="v2",
        )

    p.start()
    crash_event.wait(timeout=2.0)
    time.sleep(0.05)  # Let exception handler run
    p.stop()

    assert p.latest() is None, (
        "Worker crash must clear _latest; stale features must not remain"
    )


# ---------------------------------------------------------------------------
# Defect 8 — Non-finite drain must not be published as CanonicalData
# ---------------------------------------------------------------------------


def test_nonfinite_drain_not_published():
    """Non-finite resampler drain tail must not become CanonicalData.

    Before fix: non-finite tail zeroed → published as silent CanonicalData.
    After fix: non-finite tail discarded; only EndOfStream emitted.

    This matches the normal _process_data() path: non-finite → StreamInvalidated,
    no CanonicalData. Both paths now refuse to publish non-finite output as
    valid musical silence.
    """
    canon = AudioCanonicalizer()

    # Feed a valid DataResult to start an epoch and prime the resampler.
    frame = _make_decoded_frame(n_samples=100)
    canon.push(DataResult(frame=frame))
    # May or may not produce CanonicalData depending on resampler buffering.

    # Inject non-finite drain output by patching the resampler.
    inf_array = np.full((64, 2), float("inf"), dtype=np.float32)
    with patch.object(canon._stream, "resample_chunk", return_value=inf_array):
        eos_results = canon.push(EndOfStream())

    canonical_data = [r for r in eos_results if isinstance(r, CanonicalData)]
    eos_items = [r for r in eos_results if isinstance(r, EndOfStream)]

    assert len(canonical_data) == 0, (
        f"Non-finite drain must not produce CanonicalData; got {len(canonical_data)} frame(s)"
    )
    assert len(eos_items) == 1, "EndOfStream must still be emitted after non-finite drain"
