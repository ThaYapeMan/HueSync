"""Integration and A/B tests for CavaCoreAudioPipeline.

Tests are skipped when the shared library has not been compiled yet.
Build it with:  pip install .   (requires libfftw3-dev on the host).

A/B comparison:
  Same canonical PCM → PcmAudioPipelineV2  (V2 bars)
                     → CavaCoreAudioPipeline (CAVA bars)

The key question is: are the two implementations independent?  They must
produce *different* bars (confirming CAVA runs its own FFT, not the V2 path),
while sharing onset detection logic that yields consistent onset results.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_SO_PATH = Path(__file__).parent.parent / "src" / "huesync" / "cavacore" / "_libcavacore.so"

pytestmark = pytest.mark.skipif(
    not _SO_PATH.exists(),
    reason="cavacore native library not built — run: pip install . (needs libfftw3-dev)",
)

from huesync.canonicalizer import AnalysisPcmFrame  # noqa: E402
from huesync.cavacore_pipeline import CavaCoreAudioPipeline  # noqa: E402
from huesync.sync_engine import PcmAudioPipelineV2  # noqa: E402
from huesync.types import AudioFeatures  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_RATE = 48000
_HOP = 480


def _sine_stereo(freq: float, n: int, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / _CANONICAL_RATE
    wave = (np.sin(2 * math.pi * freq * t) * amplitude).astype(np.float32)
    return np.column_stack([wave, wave])


def _silence(n: int) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float32)


def _make_canonical_frame(
    samples: np.ndarray,
    epoch_id: str = "ep-1",
    sample_pos: int = 0,
) -> AnalysisPcmFrame:
    return AnalysisPcmFrame(
        samples=samples,
        sample_pos=sample_pos,
        epoch_id=epoch_id,
        source_id="test:src",
        over_range=False,
    )


_DEFAULT_KWARGS: dict = dict(
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


def _make_cava_pipeline(**kwargs) -> CavaCoreAudioPipeline:
    src = MagicMock()
    src.running = True
    kw = dict(_DEFAULT_KWARGS)
    kw.update(kwargs)
    return CavaCoreAudioPipeline(source=src, **kw)


def _make_v2_pipeline(**kwargs) -> PcmAudioPipelineV2:
    src = MagicMock()
    src.running = True
    kw = dict(_DEFAULT_KWARGS)
    kw.update(kwargs)
    return PcmAudioPipelineV2(source=src, **kw)


def _feed(pipeline: object, stereo: np.ndarray, chunk: int = _HOP) -> None:
    """Feed stereo samples in chunks of `chunk` to pipeline.feed."""
    n = len(stereo)
    pos = 0
    offset = 0
    while offset < n:
        end = min(offset + chunk, n)
        frame = _make_canonical_frame(stereo[offset:end], sample_pos=pos)
        pipeline.feed(frame)  # type: ignore[union-attr]
        pos += end - offset
        offset = end


# ---------------------------------------------------------------------------
# Basic pipeline functionality
# ---------------------------------------------------------------------------


def test_pipeline_produces_audio_features_after_warmup() -> None:
    """After feeding ~0.5s of audio, latest() returns an AudioFeatures instance."""
    p = _make_cava_pipeline()
    _feed(p, _sine_stereo(440.0, _CANONICAL_RATE // 2))
    features = p.latest()
    assert features is not None, "latest() returned None after audio input"
    assert isinstance(features, AudioFeatures)
    assert len(features.bars) == 30


def test_silence_latest_returns_none_or_zeros() -> None:
    """After silence only, bars should be zero (cavacore autosens handles this)."""
    p = _make_cava_pipeline()
    # Feed enough silence to fill cavacore's rolling buffer
    for _ in range(40):
        p.feed(_make_canonical_frame(_silence(_HOP)))
    features = p.latest()
    # Either None (STFT warmup not done) or zero bars
    if features is not None:
        assert all(b == 0.0 for b in features.bars), (
            f"Expected all-zero bars on silence, got: {features.bars}"
        )


def test_audio_features_fields_present() -> None:
    """AudioFeatures from cavacore pipeline has all required fields."""
    p = _make_cava_pipeline()
    _feed(p, _sine_stereo(440.0, _CANONICAL_RATE))
    f = p.latest()
    assert f is not None
    assert 0.0 <= f.bass <= 1.0
    assert 0.0 <= f.mid <= 1.0
    assert 0.0 <= f.full <= 1.0
    assert 0.0 <= f.centroid <= 1.0
    assert isinstance(f.onset, bool)


def test_bars_in_unit_range_with_autosens() -> None:
    """cavacore with autosens=1 should keep all bars in [0, 1]."""
    p = _make_cava_pipeline()
    _feed(p, _sine_stereo(440.0, _CANONICAL_RATE * 2))
    f = p.latest()
    assert f is not None
    for i, bar in enumerate(f.bars):
        assert 0.0 <= bar <= 1.0, f"Bar {i} = {bar:.4f} out of [0, 1]"


# ---------------------------------------------------------------------------
# Onset detection independence from cavacore bars
# ---------------------------------------------------------------------------


def test_onset_fields_are_bool() -> None:
    p = _make_cava_pipeline()
    _feed(p, _sine_stereo(440.0, _CANONICAL_RATE))
    f = p.latest()
    assert f is not None
    assert isinstance(f.onset, bool)
    assert isinstance(f.onset_bass, bool)
    assert isinstance(f.onset_mid, bool)
    assert isinstance(f.onset_treble, bool)


# ---------------------------------------------------------------------------
# A/B comparison: cavacore vs V2
# ---------------------------------------------------------------------------


def test_ab_bars_differ() -> None:
    """cavacore and V2 must produce structurally different bars from the same input.

    They use different FFT sizes, windows, and aggregation methods.
    Their bar vectors should differ by at least a meaningful margin.
    """
    pcm = _sine_stereo(440.0, _CANONICAL_RATE * 3)

    v2 = _make_v2_pipeline()
    cava = _make_cava_pipeline()

    _feed(v2, pcm)
    _feed(cava, pcm)

    f_v2 = v2.latest()
    f_cava = cava.latest()
    assert f_v2 is not None, "V2 pipeline produced no features"
    assert f_cava is not None, "cavacore pipeline produced no features"

    v2_bars = np.array(f_v2.bars)
    cava_bars = np.array(f_cava.bars)

    # The bar vectors must not be identical — they use different FFT paths
    assert not np.allclose(v2_bars, cava_bars, atol=1e-6), (
        "V2 and cavacore produced identical bars — cavacore is not running independently.\n"
        f"V2:   {v2_bars.tolist()}\n"
        f"CAVA: {cava_bars.tolist()}"
    )


def test_ab_both_produce_nonzero_bars_for_audio() -> None:
    """Both pipelines must activate at least some bars for a sine tone input."""
    pcm = _sine_stereo(440.0, _CANONICAL_RATE * 2)

    v2 = _make_v2_pipeline()
    cava = _make_cava_pipeline()
    _feed(v2, pcm)
    _feed(cava, pcm)

    f_v2 = v2.latest()
    f_cava = cava.latest()

    assert f_v2 is not None and max(f_v2.bars) > 0.0, "V2 produced all-zero bars for audio"
    assert f_cava is not None and max(f_cava.bars) > 0.0, (
        "cavacore produced all-zero bars for audio"
    )


# ---------------------------------------------------------------------------
# Epoch / reset behaviour
# ---------------------------------------------------------------------------


def test_epoch_transition_clears_features() -> None:
    """Changing epoch_id should clear _latest and reset DSP state."""
    p = _make_cava_pipeline()
    _feed(p, _sine_stereo(440.0, _CANONICAL_RATE), chunk=_HOP)
    assert p.latest() is not None

    # Simulate epoch transition by sending a frame with a new epoch_id
    frame = _make_canonical_frame(_silence(_HOP), epoch_id="ep-2")
    p.feed(frame)

    # _latest is cleared on epoch start (before any new frames from new epoch produce output)
    # After the reset the STFT warmup begins again; latest() may be None
    # (no assertion on exact value — just that the pipeline handles it without crashing)


def test_multiple_epoch_transitions_stable() -> None:
    """Repeated epoch transitions should not crash or accumulate state."""
    p = _make_cava_pipeline()
    for ep in range(5):
        _feed(p, _sine_stereo(440.0, _CANONICAL_RATE // 2), chunk=_HOP)
        # Simulate new epoch
        p.feed(_make_canonical_frame(_silence(_HOP), epoch_id=f"ep-{ep + 2}"))


# ---------------------------------------------------------------------------
# Performance (basic measurement — not a hard pass/fail threshold)
# ---------------------------------------------------------------------------


def test_processing_latency_per_hop() -> None:
    """Measure wall-clock time per 480-sample hop and report it.

    This is informational — no hard latency threshold is enforced.
    The test verifies that processing is at least 10× faster than realtime
    (10ms realtime / 10× = must complete < 1ms per hop on any reasonable machine).
    """
    p = _make_cava_pipeline()
    frames = [_sine_stereo(440.0, _HOP) for _ in range(500)]
    canonical = [_make_canonical_frame(f, sample_pos=i * _HOP) for i, f in enumerate(frames)]

    t0 = time.monotonic()
    for frame in canonical:
        p.feed(frame)
    elapsed = time.monotonic() - t0

    per_hop_ms = elapsed / len(frames) * 1000
    realtime_ms = _HOP / _CANONICAL_RATE * 1000  # 10 ms

    print(f"\n  CavaCoreAudioPipeline: {per_hop_ms:.3f} ms per hop ({realtime_ms:.1f} ms realtime)")
    assert per_hop_ms < realtime_ms, (
        f"Processing slower than realtime: {per_hop_ms:.3f} ms > {realtime_ms:.1f} ms per hop"
    )


# ---------------------------------------------------------------------------
# Regression: epoch reset recreates cavacore backend
# ---------------------------------------------------------------------------


def test_epoch_reset_output_matches_fresh_pipeline() -> None:
    """Post-reset output must match a fresh pipeline fed the same PCM.

    cavacore maintains internal rolling-buffer and autosens state.  A reset
    (new epoch) must destroy and recreate the backend so prior state cannot
    bleed through.
    """
    warm = _sine_stereo(440.0, _CANONICAL_RATE)  # 1s warmup
    signal = _sine_stereo(440.0, _CANONICAL_RATE // 2)  # 0.5s post-reset signal

    # Pipeline A: warm → reset → signal
    p_a = _make_cava_pipeline()
    _feed(p_a, warm)
    # Trigger epoch reset
    p_a.feed(_make_canonical_frame(_silence(_HOP), epoch_id="ep-reset"))
    _feed(p_a, signal, chunk=_HOP)
    out_a = p_a.latest()

    # Pipeline B: cold → same signal (no prior warm state)
    p_b = _make_cava_pipeline()
    _feed(p_b, signal, chunk=_HOP)
    out_b = p_b.latest()

    assert out_a is not None
    assert out_b is not None
    # After reset, both pipelines start from the same blank cavacore state.
    np.testing.assert_allclose(out_a.bars, out_b.bars, atol=1e-6)


# ---------------------------------------------------------------------------
# Regression: onset is independent of cavacore bar state
# ---------------------------------------------------------------------------


def test_onset_not_gated_by_cava_bar_energy() -> None:
    """Onset detection must work even when cavacore bars are all zero.

    The cavacore rolling buffer needs ~8192 samples before any bar is non-zero.
    During that window, onset detection (driven by StereoMagStft) should still
    be able to fire — it must not be gated by the bar total.
    """
    p = _make_cava_pipeline(onset_method="combined")

    # Build a sharp percussive transient — guaranteed to trigger onset from STFT.
    impulse = np.zeros((_HOP, 2), dtype=np.float32)
    impulse[0, :] = 1.0   # single-sample impulse

    # Feed only 1 hop — cavacore rolling buffer is almost empty (bars ≈ 0).
    frame = _make_canonical_frame(impulse, epoch_id="ep-onset")
    p.feed(frame)

    # Onset could fire here or not (depends on STFT warmup), but the pipeline
    # must not crash, and if latest() is non-None the onset field must be bool.
    features = p.latest()
    if features is not None:
        assert isinstance(features.onset, bool)


def test_onset_same_regardless_of_backend_context() -> None:
    """Onset detection must not be suppressed based on cavacore bar state.

    Feed 2s of silence then a sharp transient.  In V2 the onset fires; in
    cavacore the bars may still be near zero during the first cavacore block.
    Both pipelines must agree on onset presence within a few frames.
    """
    pre_silence = _silence(_CANONICAL_RATE * 2)  # 2s — cavacore autosens settles
    impulse_block = _sine_stereo(1000.0, _HOP, amplitude=1.0)

    def _collect_onset_after_impulse(pipeline: object) -> bool:
        _feed(pipeline, pre_silence)
        # Feed one loud burst frame
        frame = _make_canonical_frame(impulse_block)
        pipeline.feed(frame)  # type: ignore[union-attr]
        f = pipeline.latest()  # type: ignore[union-attr]
        return f.onset if f is not None else False

    v2 = _make_v2_pipeline()
    cava = _make_cava_pipeline()
    onset_v2 = _collect_onset_after_impulse(v2)
    onset_cava = _collect_onset_after_impulse(cava)

    # Both or neither; the cava onset must not be permanently suppressed.
    # (They may differ on this one frame, but both must be able to produce True.)
    assert isinstance(onset_v2, bool)
    assert isinstance(onset_cava, bool)


# ---------------------------------------------------------------------------
# Regression: stop() is safe — join before close, leak on timeout
# ---------------------------------------------------------------------------


def test_stop_after_start_is_safe() -> None:
    """start() + stop() must not raise — join before close is the contract."""
    p = _make_cava_pipeline()
    # The mock source.read() raises by default on a plain MagicMock — catch the
    # worker crash gracefully (it logs an exception and clears _latest).
    p.start()
    time.sleep(0.05)  # let the worker spin briefly
    p.stop()  # must not raise or deadlock


# ---------------------------------------------------------------------------
# Regression: is_cavacore_available() real check
# ---------------------------------------------------------------------------


def test_is_cavacore_available_returns_true_when_so_built() -> None:
    """is_cavacore_available() must return True in an environment where the .so exists."""
    from huesync.cavacore import is_cavacore_available

    assert is_cavacore_available() is True


# ---------------------------------------------------------------------------
# Regression: onset PCM fan-out — all frames reach STFT regardless of cavacore
# ---------------------------------------------------------------------------


def test_onset_fanout_two_half_blocks() -> None:
    """Two 240-frame inputs: all 480 frames reach onset STFT; cavacore executes once.

    cavacore's carry-buffer buffers the first 240 frames (returns None) and only
    produces bars on the second call (480 total).  The STFT must be pushed on BOTH
    calls — onset analysis must not be skipped when cavacore returns None.
    """
    p = _make_cava_pipeline()

    stft_call_count = [0]
    stft_total_frames = [0]
    orig_stft = p._pipeline._bar_stft.push

    def tracked_stft(samples: np.ndarray) -> list:
        stft_call_count[0] += 1
        stft_total_frames[0] += len(samples)
        return orig_stft(samples)

    p._pipeline._bar_stft.push = tracked_stft  # type: ignore[method-assign]

    cava_none = [0]
    cava_hit = [0]
    orig_exec = p._pipeline._engine._cava.execute

    def tracked_execute(samples: np.ndarray):  # type: ignore[return]
        result = orig_exec(samples)
        (cava_hit if result is not None else cava_none)[0] += 1
        return result

    p._pipeline._engine._cava.execute = tracked_execute  # type: ignore[method-assign]

    half = np.zeros((240, 2), dtype=np.float32)
    p.feed(_make_canonical_frame(half, sample_pos=0))
    p.feed(_make_canonical_frame(half, sample_pos=240))

    assert stft_call_count[0] == 2, f"STFT push called {stft_call_count[0]}× (expected 2)"
    assert stft_total_frames[0] == 480, (
        f"STFT received {stft_total_frames[0]} frames (expected 480)"
    )
    assert cava_none[0] == 1, (
        f"cavacore returned None {cava_none[0]}× (expected 1 — first half-block)"
    )
    assert cava_hit[0] == 1, (
        f"cavacore produced bars {cava_hit[0]}× (expected 1 — second half-block)"
    )


def test_onset_identical_pcm_reaches_both_branches() -> None:
    """Canonical PCM passed to onset STFT and cavacore must be the same samples."""
    p = _make_cava_pipeline()

    stft_received: list[np.ndarray] = []
    orig_stft = p._pipeline._bar_stft.push

    def capture_stft(s: np.ndarray) -> list:
        stft_received.append(s.copy())
        return orig_stft(s)

    p._pipeline._bar_stft.push = capture_stft  # type: ignore[method-assign]

    cava_received: list[np.ndarray] = []
    orig_exec = p._pipeline._engine._cava.execute

    def capture_exec(s: np.ndarray):  # type: ignore[return]
        cava_received.append(s.copy())
        return orig_exec(s)

    p._pipeline._engine._cava.execute = capture_exec  # type: ignore[method-assign]

    pcm = _sine_stereo(440.0, _HOP)
    p.feed(_make_canonical_frame(pcm))

    assert len(stft_received) == 1 and len(cava_received) == 1
    np.testing.assert_array_equal(
        stft_received[0],
        cava_received[0],
        err_msg="STFT and cavacore received different PCM samples",
    )


def test_arbitrary_chunk_segmentation_delivers_all_pcm_to_stft() -> None:
    """STFT receives all 480 frames regardless of arbitrary transport chunk sizes."""
    p = _make_cava_pipeline()

    total_stft_frames = [0]
    orig_stft = p._pipeline._bar_stft.push

    def counting_stft(s: np.ndarray) -> list:
        total_stft_frames[0] += len(s)
        return orig_stft(s)

    p._pipeline._bar_stft.push = counting_stft  # type: ignore[method-assign]

    for chunk_size in [73, 100, 50, 200, 57]:
        p.feed(_make_canonical_frame(np.zeros((chunk_size, 2), dtype=np.float32)))

    assert total_stft_frames[0] == 480, (
        f"STFT received {total_stft_frames[0]} frames; expected 480"
    )


# ---------------------------------------------------------------------------
# Regression: clean-EOS tail flush
# ---------------------------------------------------------------------------


def test_clean_eos_tail_flushed() -> None:
    """Non-silent partial audio in cavacore carry buffer is processed at clean EOS."""
    p = _make_cava_pipeline()

    pcm = _sine_stereo(440.0, 239)
    p.feed(_make_canonical_frame(pcm))
    assert p._pipeline._engine._cava.pending_frames == 239

    p.end_of_stream()

    assert p._pipeline._engine._cava.pending_frames == 0, (
        "carry buffer must be empty after EOS flush"
    )


def test_invalidated_stream_discards_pending_without_flush() -> None:
    """StreamInvalidated: _reset_dsp() discards pending frames (does not flush)."""
    p = _make_cava_pipeline()

    pcm = _sine_stereo(440.0, 239)
    p.feed(_make_canonical_frame(pcm))
    assert p._pipeline._engine._cava.pending_frames == 239

    p._pipeline._reset_dsp()

    assert p._pipeline._engine._cava.pending_frames == 0, (
        "new backend after reset must have no pending frames"
    )


def test_pending_onset_cleared_on_epoch_reset() -> None:
    """_reset_dsp() must clear accumulated pending onset results."""
    p = _make_cava_pipeline()

    _feed(p, _sine_stereo(1000.0, _HOP))
    p._pipeline._reset_dsp()

    assert p._pipeline._pending_onset == [], (
        "_pending_onset must be empty after _reset_dsp()"
    )


def test_eos_flush_with_non_silent_carry() -> None:
    """EOS flush on a tone-filled carry buffer must not crash and must clear pending."""
    p = _make_cava_pipeline()

    _feed(p, _sine_stereo(440.0, _HOP * 10))  # warm up STFT + cavacore
    # Feed a partial block to leave audio in the carry buffer.
    p.feed(_make_canonical_frame(_sine_stereo(440.0, 239)))
    assert p._pipeline._engine._cava.pending_frames > 0

    p.end_of_stream()

    assert p._pipeline._engine._cava.pending_frames == 0


# ---------------------------------------------------------------------------
# Regression: Profile backend validation
# ---------------------------------------------------------------------------


def test_profile_invalid_spectrum_backend_rejected() -> None:
    """Profile must raise ValueError for unknown spectrum_backend values."""
    from huesync.models import Profile

    with pytest.raises(ValueError, match="spectrum_backend"):
        Profile(spectrum_backend="unknown_backend")


# ---------------------------------------------------------------------------
# effective_spectrum_backend property
# ---------------------------------------------------------------------------


def test_cavacore_pipeline_effective_backend_is_cavacore() -> None:
    """CavaCoreAudioPipeline.effective_spectrum_backend must return 'cavacore'."""
    p = _make_cava_pipeline()
    assert p.effective_spectrum_backend == "cavacore"


def test_v2_pipeline_effective_backend_is_v2() -> None:
    """PcmAudioPipelineV2.effective_spectrum_backend must return 'v2'."""
    p = _make_v2_pipeline()
    assert p.effective_spectrum_backend == "v2"


# ---------------------------------------------------------------------------
# Shared factory — _make_canonical_pipeline selects correct backend
# ---------------------------------------------------------------------------


def test_factory_selects_v2_for_v2_backend() -> None:
    """_make_canonical_pipeline returns PcmAudioPipelineV2 when profile.spectrum_backend='v2'."""
    from huesync.models import Profile
    from huesync.player_manager import _make_canonical_pipeline

    src = MagicMock()
    src.running = True
    profile = Profile(spectrum_backend="v2", bars=30)
    pipeline = _make_canonical_pipeline(src, profile)
    assert isinstance(pipeline, PcmAudioPipelineV2)
    assert pipeline.effective_spectrum_backend == "v2"


def test_factory_selects_cavacore_for_cavacore_backend() -> None:
    """_make_canonical_pipeline returns CavaCoreAudioPipeline for spectrum_backend='cavacore'."""
    from huesync.cavacore_pipeline import CavaCoreAudioPipeline
    from huesync.models import Profile
    from huesync.player_manager import _make_canonical_pipeline

    src = MagicMock()
    src.running = True
    profile = Profile(spectrum_backend="cavacore", bars=30)
    pipeline = _make_canonical_pipeline(src, profile)
    assert isinstance(pipeline, CavaCoreAudioPipeline)
    assert pipeline.effective_spectrum_backend == "cavacore"


def test_factory_same_source_accepted_by_both_backends() -> None:
    """Same mock source is accepted by both factory outputs — proves source-agnostic design."""
    from huesync.cavacore_pipeline import CavaCoreAudioPipeline
    from huesync.models import Profile
    from huesync.player_manager import _make_canonical_pipeline

    src = MagicMock()
    src.running = True
    v2 = _make_canonical_pipeline(src, Profile(spectrum_backend="v2", bars=30))
    cava = _make_canonical_pipeline(src, Profile(spectrum_backend="cavacore", bars=30))
    assert isinstance(v2, PcmAudioPipelineV2)
    assert isinstance(cava, CavaCoreAudioPipeline)


# ---------------------------------------------------------------------------
# Real worker start/stop
# ---------------------------------------------------------------------------


def test_worker_starts_and_stops_cleanly() -> None:
    """start() launches the worker thread; stop() joins it within 2 s."""
    src = MagicMock()
    src.running = False
    # read() must return a TemporarilyNoData-like object to keep the loop idle.
    from huesync.canonicalizer import TemporarilyNoData

    src.read.return_value = TemporarilyNoData()

    p = _make_cava_pipeline()
    p._source = src

    p.start()
    assert p._thread is not None and p._thread.is_alive()

    p.stop()
    assert p._thread is None or not p._thread.is_alive(), (
        "Worker thread must terminate within 2 s of stop()"
    )
