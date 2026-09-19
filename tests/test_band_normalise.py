from unittest.mock import MagicMock

import pytest

from huesync.models import Analyser, Profile
from huesync.spectrum_engine import ProcessorUpdate, V2SpectrumEngine
from huesync.sync_engine import CanonicalAnalysisPipeline, CavaPipeline, SyncEngine


def pipeline(enabled=False):
    return CanonicalAnalysisPipeline(
        object(), V2SpectrumEngine(n_bars=10, lower_hz=50, upper_hz=12000),
        "combined", .1, .9, 3, 2, 250, 2000, band_normalise=enabled,
    )


def publish(cap, bars, pos):
    return cap._publish_by_interval(
        epoch_id="test", spectrum_updates=[ProcessorUpdate(
            processor_id="spectrum:v2", sample_start=pos, sample_end=pos+480, bars=bars,
        )], other_updates=[], clamp_end=None,
    )[0]


def test_balances_constant_bars_and_preserves_all_raw_aggregates_and_history():
    cap, bypass = pipeline(True), pipeline(False)
    raw = [.6] * 5 + [.2] * 5
    for pos in range(0, 48000, 480):
        normal = publish(cap, raw, pos).features
        plain = publish(bypass, raw, pos).features
        assert plain.bars == raw
        assert normal.bars == pytest.approx([85/255] * 10)
        for name in ("full", "bass", "mid", "centroid", "relative_exertion"):
            assert getattr(normal, name) == getattr(plain, name)
    transient = publish(cap, [.95]*5 + [.2]*5, 48000).features
    assert transient.bars[0] > .5
    assert transient.bars[-1] == pytest.approx(85/255)
    delayed = cap._publish_by_interval(
        epoch_id="test", spectrum_updates=[], other_updates=[ProcessorUpdate(
            processor_id="beat", sample_start=47520, sample_end=48000, onset=True,
        )], clamp_end=None,
    )[0]
    assert delayed.features.bars == normal.bars
    assert delayed.features.full == plain.full
    assert delayed.carried_spectrum_interval == (47520, 48000)


def test_live_toggle_seeds_first_frame_and_preserves_worker_ownership():
    cap = pipeline()
    engine = SyncEngine(None, Profile(), analyser=cap)
    cap.start = MagicMock()
    cap.stop = MagicMock()
    engine.update_render(Profile(band_normalise=True))
    assert publish(cap, [.6]*5+[.2]*5, 0).features.bars == pytest.approx([85/255]*10)
    engine.update_render(Profile(band_normalise=False))
    assert publish(cap, [.6]*10, 480).features.bars == [.6]*10
    engine.update_render(Profile(band_normalise=True))
    assert publish(cap, [.9]*10, 960).features.bars == pytest.approx([85/255]*10)
    cap.start.assert_not_called()
    cap.stop.assert_not_called()
    cap._reset_dsp()
    assert publish(cap, [.1]*10, 0).features.bars == pytest.approx([85/255]*10)


def test_cava_stays_unconditionally_normalised():
    from pathlib import Path
    cap = CavaPipeline(Path("/tmp/unused-band-normalise"), 10)
    cap._reader = MagicMock()
    cap._reader.latest_frame.return_value = bytes([153]*5+[51]*5)
    assert Analyser().band_normalise is False
    assert cap.latest().bars == pytest.approx([85/255]*10)


def test_preview_pair_is_raw_and_effect_input_is_unchanged():
    cap = pipeline(True)
    raw = [.6]*5 + [.2]*5
    record = publish(cap, raw, 0)
    assert cap.preview_spectrum() == (raw, record.features.bars)
    assert record.features.bars == pytest.approx([85/255]*10)
    cap.update_band_normalisation(False, 3)
    # Toggle only appears with the next Spectrum frame, not mixed across frames.
    assert cap.preview_spectrum()[1] is not None
    record = publish(cap, raw, 480)
    assert cap.preview_spectrum() == (raw, None)
    assert record.features.bars == raw
