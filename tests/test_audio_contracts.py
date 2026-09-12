"""Tests for Phase 1 audio contract types in huesync.canonicalizer."""

import numpy as np
import pytest

from huesync.canonicalizer import (
    AnalysisPcmFrame,
    AudioCanonicalizer,
    CanonicalData,
    DataResult,
    DecodedSourceFrame,
    EndOfStream,
    FeatureStatus,
    InvalidationCause,
    OnsetEvent,
    SourceReadResult,
    SpectrumLayout,
    StreamInvalidated,
    TemporarilyNoData,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stereo_f32(n: int = 10) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float32)


def _mono_f32(n: int = 10) -> np.ndarray:
    return np.zeros((n, 1), dtype=np.float32)


def _decoded(channels: int = 2, n: int = 10, **kw) -> DecodedSourceFrame:
    arr = np.zeros((n, channels), dtype=np.float32)
    defaults: dict = dict(
        samples=arr,
        sample_rate=44100,
        channels=channels,
        source_id="test:src",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    defaults.update(kw)
    return DecodedSourceFrame(**defaults)


def _analysis(n: int = 10, **kw) -> AnalysisPcmFrame:
    arr = np.zeros((n, 2), dtype=np.float32)
    defaults: dict = dict(
        samples=arr,
        sample_pos=0,
        epoch_id="ep-1",
        source_id="test:src",
        over_range=False,
    )
    defaults.update(kw)
    return AnalysisPcmFrame(**defaults)


# ---------------------------------------------------------------------------
# DecodedSourceFrame — valid construction
# ---------------------------------------------------------------------------


def test_decoded_mono_valid():
    frame = _decoded(channels=1)
    assert frame.samples.shape == (10, 1)
    assert frame.channels == 1
    assert frame.sample_rate == 44100
    assert frame.source_id == "test:src"
    assert frame.source_sample_pos is None
    assert frame.wall_ns is None
    assert not frame.over_range


def test_decoded_stereo_valid():
    frame = _decoded(channels=2)
    assert frame.samples.shape == (10, 2)
    assert frame.channels == 2


def test_decoded_over_range_field():
    frame = _decoded(over_range=True)
    assert frame.over_range is True


def test_decoded_source_sample_pos_set():
    frame = _decoded(source_sample_pos=1024)
    assert frame.source_sample_pos == 1024


def test_decoded_wall_ns_set():
    frame = _decoded(wall_ns=1_000_000_000)
    assert frame.wall_ns == 1_000_000_000


# ---------------------------------------------------------------------------
# DecodedSourceFrame — immutability and ownership
# ---------------------------------------------------------------------------


def test_decoded_metadata_immutable():
    frame = _decoded()
    with pytest.raises((AttributeError, TypeError)):
        frame.sample_rate = 48000  # type: ignore[misc]


def test_decoded_samples_readonly():
    frame = _decoded()
    with pytest.raises(ValueError, match="read-only"):
        frame.samples[0, 0] = 1.0


def test_decoded_mutation_isolation():
    """External array mutated after construction must not affect published samples."""
    arr = np.zeros((10, 2), dtype=np.float32)
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=44100,
        channels=2,
        source_id="test:src",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    arr[0, 0] = 99.0
    assert frame.samples[0, 0] == 0.0


# ---------------------------------------------------------------------------
# DecodedSourceFrame — validation
# ---------------------------------------------------------------------------


def test_decoded_wrong_dtype_rejected():
    arr = np.zeros((10, 2), dtype=np.int16)
    with pytest.raises(ValueError, match="float32"):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=2,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_float64_rejected():
    arr = np.zeros((10, 2), dtype=np.float64)
    with pytest.raises(ValueError, match="float32"):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=2,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_wrong_shape_1d_rejected():
    arr = np.zeros((10,), dtype=np.float32)
    with pytest.raises(ValueError):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=1,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_channels_gt2_rejected():
    arr = np.zeros((10, 3), dtype=np.float32)
    with pytest.raises(ValueError):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=3,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_shape_channels_mismatch_rejected():
    arr = np.zeros((10, 1), dtype=np.float32)
    with pytest.raises(ValueError):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=2,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_zero_sample_rate_rejected():
    arr = np.zeros((10, 2), dtype=np.float32)
    with pytest.raises(ValueError):
        DecodedSourceFrame(
            samples=arr, sample_rate=0, channels=2,
            source_id="test:src", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


def test_decoded_empty_source_id_rejected():
    arr = np.zeros((10, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="source_id"):
        DecodedSourceFrame(
            samples=arr, sample_rate=44100, channels=2,
            source_id="", source_sample_pos=None,
            over_range=False, wall_ns=None,
        )


# ---------------------------------------------------------------------------
# AnalysisPcmFrame — valid construction
# ---------------------------------------------------------------------------


def test_analysis_valid():
    frame = _analysis()
    assert frame.samples.shape == (10, 2)
    assert frame.sample_pos == 0
    assert frame.epoch_id == "ep-1"
    assert frame.source_id == "test:src"
    assert not frame.over_range


def test_analysis_sample_pos_nonzero():
    frame = _analysis(sample_pos=4800)
    assert frame.sample_pos == 4800


def test_analysis_class_constants():
    assert AnalysisPcmFrame.SAMPLE_RATE == 48000
    assert AnalysisPcmFrame.CHANNELS == 2


def test_analysis_class_constants_via_instance():
    frame = _analysis()
    assert frame.SAMPLE_RATE == 48000
    assert frame.CHANNELS == 2


# ---------------------------------------------------------------------------
# AnalysisPcmFrame — immutability and ownership
# ---------------------------------------------------------------------------


def test_analysis_metadata_immutable():
    frame = _analysis()
    with pytest.raises((AttributeError, TypeError)):
        frame.sample_pos = 9999  # type: ignore[misc]


def test_analysis_samples_readonly():
    frame = _analysis()
    with pytest.raises(ValueError, match="read-only"):
        frame.samples[0, 0] = 1.0


def test_analysis_mutation_isolation():
    arr = np.zeros((10, 2), dtype=np.float32)
    frame = AnalysisPcmFrame(
        samples=arr, sample_pos=0, epoch_id="ep-1",
        source_id="test:src", over_range=False,
    )
    arr[0, 0] = 55.0
    assert frame.samples[0, 0] == 0.0


# ---------------------------------------------------------------------------
# AnalysisPcmFrame — validation
# ---------------------------------------------------------------------------


def test_analysis_wrong_shape_rejected():
    arr = np.zeros((10, 1), dtype=np.float32)
    with pytest.raises(ValueError):
        AnalysisPcmFrame(
            samples=arr, sample_pos=0, epoch_id="ep-1",
            source_id="test:src", over_range=False,
        )


def test_analysis_3d_shape_rejected():
    arr = np.zeros((10, 2, 1), dtype=np.float32)
    with pytest.raises(ValueError):
        AnalysisPcmFrame(
            samples=arr, sample_pos=0, epoch_id="ep-1",
            source_id="test:src", over_range=False,
        )


def test_analysis_wrong_dtype_rejected():
    arr = np.zeros((10, 2), dtype=np.float64)
    with pytest.raises(ValueError, match="float32"):
        AnalysisPcmFrame(
            samples=arr, sample_pos=0, epoch_id="ep-1",
            source_id="test:src", over_range=False,
        )


def test_analysis_negative_sample_pos_rejected():
    with pytest.raises(ValueError, match="sample_pos"):
        _analysis(sample_pos=-1)


def test_analysis_empty_epoch_id_rejected():
    with pytest.raises(ValueError, match="epoch_id"):
        _analysis(epoch_id="")


def test_analysis_empty_source_id_rejected():
    with pytest.raises(ValueError, match="source_id"):
        _analysis(source_id="")


# ---------------------------------------------------------------------------
# Lifecycle result types
# ---------------------------------------------------------------------------


def test_data_result_construction():
    frame = _decoded()
    result = DataResult(frame=frame)
    assert result.frame is frame


def test_temporarily_no_data_construction():
    result = TemporarilyNoData()
    assert isinstance(result, TemporarilyNoData)


def test_stream_invalidated_with_unknown_loss():
    result = StreamInvalidated(cause=InvalidationCause.SEEK)
    assert result.cause == InvalidationCause.SEEK
    assert result.known_lost_samples is None


def test_stream_invalidated_with_known_loss():
    result = StreamInvalidated(
        cause=InvalidationCause.RESTART, known_lost_samples=4410
    )
    assert result.known_lost_samples == 4410


def test_stream_invalidated_zero_loss_valid():
    result = StreamInvalidated(cause=InvalidationCause.UNKNOWN, known_lost_samples=0)
    assert result.known_lost_samples == 0


def test_stream_invalidated_negative_loss_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        StreamInvalidated(cause=InvalidationCause.RECONNECT, known_lost_samples=-1)


def test_end_of_stream_construction():
    result = EndOfStream()
    assert isinstance(result, EndOfStream)


def test_lifecycle_types_are_frozen():
    r = StreamInvalidated(cause=InvalidationCause.SEEK)
    with pytest.raises((AttributeError, TypeError)):
        r.cause = InvalidationCause.RESTART  # type: ignore[misc]


def test_source_read_result_union_members():
    """All four members are valid SourceReadResult instances."""
    frame = _decoded()
    results: list[SourceReadResult] = [
        DataResult(frame=frame),
        TemporarilyNoData(),
        StreamInvalidated(cause=InvalidationCause.UNKNOWN),
        EndOfStream(),
    ]
    assert len(results) == 4


# ---------------------------------------------------------------------------
# CanonicalData
# ---------------------------------------------------------------------------


def test_canonical_data_construction():
    frame = _analysis()
    cd = CanonicalData(frame=frame)
    assert cd.frame is frame


def test_canonical_lifecycle_variants():
    from huesync.canonicalizer import CanonicalReadResult  # noqa: F401 — import check

    frame = _analysis()
    variants = [
        CanonicalData(frame=frame),
        TemporarilyNoData(),
        StreamInvalidated(cause=InvalidationCause.FORMAT_CHANGE),
        EndOfStream(),
    ]
    assert len(variants) == 4


# ---------------------------------------------------------------------------
# FeatureStatus
# ---------------------------------------------------------------------------


def test_feature_status_values():
    assert FeatureStatus.VALID.value == "valid"
    assert FeatureStatus.WARMING_UP.value == "warming_up"
    assert FeatureStatus.UNAVAILABLE.value == "unavailable"
    assert FeatureStatus.DISABLED.value == "disabled"
    assert FeatureStatus.INVALID.value == "invalid"


def test_feature_status_five_members():
    assert len(FeatureStatus) == 5


# ---------------------------------------------------------------------------
# OnsetEvent
# ---------------------------------------------------------------------------


def test_onset_event_valid():
    evt = OnsetEvent(
        epoch_id="ep-1",
        event_sample_pos=4800,
        strength=0.75,
        bands=frozenset({"bass", "mid"}),
    )
    assert evt.epoch_id == "ep-1"
    assert evt.event_sample_pos == 4800
    assert evt.strength == 0.75
    assert evt.bands == frozenset({"bass", "mid"})


def test_onset_event_empty_bands_valid():
    evt = OnsetEvent(
        epoch_id="ep-1", event_sample_pos=0, strength=0.0, bands=frozenset()
    )
    assert evt.bands == frozenset()


def test_onset_event_strength_zero_valid():
    evt = OnsetEvent(epoch_id="ep-1", event_sample_pos=0, strength=0.0, bands=frozenset())
    assert evt.strength == 0.0


def test_onset_event_strength_one_valid():
    evt = OnsetEvent(epoch_id="ep-1", event_sample_pos=0, strength=1.0, bands=frozenset())
    assert evt.strength == 1.0


def test_onset_event_bands_immutable():
    evt = OnsetEvent(
        epoch_id="ep-1", event_sample_pos=0, strength=0.5, bands=frozenset({"bass"})
    )
    with pytest.raises((AttributeError, TypeError)):
        evt.bands = frozenset({"mid"})  # type: ignore[misc]


def test_onset_event_invalid_strength_above_one():
    with pytest.raises(ValueError, match="strength"):
        OnsetEvent(epoch_id="ep-1", event_sample_pos=0, strength=1.001, bands=frozenset())


def test_onset_event_invalid_strength_below_zero():
    with pytest.raises(ValueError, match="strength"):
        OnsetEvent(epoch_id="ep-1", event_sample_pos=0, strength=-0.001, bands=frozenset())


def test_onset_event_negative_sample_pos_rejected():
    with pytest.raises(ValueError, match="event_sample_pos"):
        OnsetEvent(epoch_id="ep-1", event_sample_pos=-1, strength=0.5, bands=frozenset())


def test_onset_event_empty_epoch_id_rejected():
    with pytest.raises(ValueError, match="epoch_id"):
        OnsetEvent(epoch_id="", event_sample_pos=0, strength=0.5, bands=frozenset())


# ---------------------------------------------------------------------------
# SpectrumLayout — defaults and construction
# ---------------------------------------------------------------------------


def test_spectrum_layout_defaults():
    layout = SpectrumLayout()
    assert layout.bar_count == 30
    assert layout.lower_cutoff_hz == 50.0
    assert layout.upper_cutoff_hz == 12000.0
    assert layout.bass_boundary_hz == 250.0
    assert layout.mid_boundary_hz == 2000.0


def test_spectrum_layout_immutable():
    layout = SpectrumLayout()
    with pytest.raises((AttributeError, TypeError)):
        layout.bar_count = 60  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SpectrumLayout — hz_to_bar_frac
# ---------------------------------------------------------------------------


def test_hz_to_bar_frac_lower_endpoint():
    layout = SpectrumLayout()
    frac = layout.hz_to_bar_frac(50.0)
    assert abs(frac - 0.0) < 1e-9


def test_hz_to_bar_frac_upper_endpoint():
    layout = SpectrumLayout()
    frac = layout.hz_to_bar_frac(12000.0)
    assert abs(frac - 1.0) < 1e-9


def test_hz_to_bar_frac_midpoint_is_log_midpoint():
    layout = SpectrumLayout()
    import math
    mid_hz = 10 ** ((math.log10(50.0) + math.log10(12000.0)) / 2)
    frac = layout.hz_to_bar_frac(mid_hz)
    assert abs(frac - 0.5) < 1e-9


def test_hz_to_bar_frac_not_clamped_outside_range():
    layout = SpectrumLayout()
    frac_below = layout.hz_to_bar_frac(1.0)
    assert frac_below < 0.0
    frac_above = layout.hz_to_bar_frac(100_000.0)
    assert frac_above > 1.0


# ---------------------------------------------------------------------------
# SpectrumLayout — bass_bar_index / mid_bar_index
# ---------------------------------------------------------------------------


def test_bass_bar_index_default():
    layout = SpectrumLayout()
    idx = layout.bass_bar_index()
    assert idx == 9


def test_mid_bar_index_default():
    layout = SpectrumLayout()
    idx = layout.mid_bar_index()
    assert idx == 20


def test_bar_indices_bounded_at_lower_cutoff():
    layout = SpectrumLayout(bass_boundary_hz=50.0, mid_boundary_hz=50.0)
    assert layout.bass_bar_index() == 0
    assert layout.mid_bar_index() == 0


def test_bar_indices_bounded_at_upper_cutoff():
    layout = SpectrumLayout(bass_boundary_hz=12000.0, mid_boundary_hz=12000.0)
    assert layout.bass_bar_index() == 30
    assert layout.mid_bar_index() == 30


def test_bar_index_deterministic():
    layout = SpectrumLayout()
    assert layout.bass_bar_index() == layout.bass_bar_index()
    assert layout.mid_bar_index() == layout.mid_bar_index()


def test_bass_bar_index_never_exceeds_bar_count():
    layout = SpectrumLayout(bar_count=10, lower_cutoff_hz=20.0, upper_cutoff_hz=20000.0,
                            bass_boundary_hz=20000.0, mid_boundary_hz=20000.0)
    assert layout.bass_bar_index() <= layout.bar_count


def test_mid_bar_index_never_below_zero():
    layout = SpectrumLayout(bar_count=10, lower_cutoff_hz=20.0, upper_cutoff_hz=20000.0,
                            bass_boundary_hz=20.0, mid_boundary_hz=20.0)
    assert layout.mid_bar_index() >= 0


# ---------------------------------------------------------------------------
# SpectrumLayout — validation
# ---------------------------------------------------------------------------


def test_spectrum_layout_zero_bar_count_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(bar_count=0)


def test_spectrum_layout_negative_bar_count_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(bar_count=-1)


def test_spectrum_layout_zero_lower_hz_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(lower_cutoff_hz=0.0)


def test_spectrum_layout_upper_le_lower_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(lower_cutoff_hz=100.0, upper_cutoff_hz=100.0,
                       bass_boundary_hz=100.0, mid_boundary_hz=100.0)


def test_spectrum_layout_bass_below_lower_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(lower_cutoff_hz=50.0, bass_boundary_hz=40.0, mid_boundary_hz=2000.0)


def test_spectrum_layout_mid_above_upper_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(upper_cutoff_hz=12000.0, bass_boundary_hz=250.0,
                       mid_boundary_hz=15000.0)


def test_spectrum_layout_bass_gt_mid_rejected():
    with pytest.raises(ValueError):
        SpectrumLayout(bass_boundary_hz=2000.0, mid_boundary_hz=250.0)


# ---------------------------------------------------------------------------
# AudioCanonicalizer stub
# ---------------------------------------------------------------------------


def test_canonicalizer_push_raises():
    ac = AudioCanonicalizer()
    with pytest.raises(NotImplementedError):
        ac.push(TemporarilyNoData())


def test_canonicalizer_reset_raises():
    ac = AudioCanonicalizer()
    with pytest.raises(NotImplementedError):
        ac.reset()
