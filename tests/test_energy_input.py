import asyncio
import math
from unittest.mock import MagicMock

import pytest

from huesync.energy_input import EnergyInput
from huesync.models import EnergyProfile, Profile
from huesync.spectrum_engine import ProcessorUpdate
from huesync.sync_engine import LayerMixer, SyncEngine, _smoothstep
from huesync.types import AudioFeatures


def features(lufs=-11.0, sustained=0.0, full=.2):
    return AudioFeatures([.2]*10, .2, .2, full, .5,
                         sustained_energy=sustained, loudness_momentary_lufs=lufs)


@pytest.mark.parametrize("mode,expected", [
    ("sustained", 0.0), ("loudness_fixed", 19/22), ("loudness_adaptive", 19/22),
])
def test_selected_input_precedes_unchanged_smoothstep_and_response(mode, expected):
    profile = Profile(energy_source=mode)
    mixer = LayerMixer(profile, profile)
    mixer.render(features(), 0)
    assert mixer.last_energy_input == pytest.approx(expected)
    assert mixer.mix == pytest.approx(.1 * _smoothstep(expected, .3, .7))


def test_constant_loud_programme_stays_high_in_fixed_mode_and_default_is_unchanged():
    fixed = EnergyInput(Profile(energy_source="loudness_fixed"))
    sustained = EnergyInput(Profile())
    for second in range(180):
        assert fixed.select(features(), second) == pytest.approx(19/22)
        assert sustained.select(features(), second) == 0
    assert sustained.select(features(sustained=None, full=.17), 181) == .17


@pytest.mark.parametrize("mode", ["loudness_fixed", "loudness_adaptive"])
@pytest.mark.parametrize("value", [None, -math.inf, math.inf, math.nan])
def test_silence_and_missing_input_are_zero_without_training(mode, value):
    selector = EnergyInput(Profile(energy_source=mode))
    before = selector.adaptive_floor, selector.adaptive_ceiling
    assert selector.select(features(value), 0) == 0
    assert selector.select(features(value), 100) == 0
    assert (selector.adaptive_floor, selector.adaptive_ceiling) == before


def test_fixed_clamps_and_honours_custom_bounds():
    selector = EnergyInput(
        Profile(energy_source="loudness_fixed", lufs_floor=-40, lufs_ceiling=-10))
    for value, expected in [(-50, 0), (-40, 0), (-25, .5), (-10, 1), (0, 1)]:
        assert selector.select(features(value), 0) == expected


def test_adaptive_expands_quickly_contracts_slowly_and_keeps_six_lu_span():
    selector = EnergyInput(Profile(energy_source="loudness_adaptive"))
    selector.select(features(-20), 0)
    before = selector.adaptive_ceiling - selector.adaptive_floor
    selector.select(features(0), 1)
    raised = selector.adaptive_ceiling
    assert raised > -4
    assert selector.adaptive_ceiling - selector.adaptive_floor > before
    selector.select(features(-20), 2)
    assert -1 < selector.adaptive_ceiling - raised < 0
    for second in range(3, 1000):
        value = selector.select(features(-20), second)
        assert 0 <= value <= 1
        assert selector.adaptive_ceiling - selector.adaptive_floor >= 6 - 1e-9
    assert value == pytest.approx(.5, abs=.001)


@pytest.mark.parametrize("kwargs", [
    {"energy_source": "unknown"}, {"lufs_floor": -8, "lufs_ceiling": -30},
    {"lufs_floor": -8, "lufs_ceiling": -8}, {"lufs_floor": math.nan},
    {"lufs_ceiling": math.inf}, {"adaptation_tau_s": 0}, {"adaptation_tau_s": math.inf},
])
def test_profile_validation(kwargs):
    with pytest.raises(ValueError):
        EnergyProfile(**kwargs)
    with pytest.raises(ValueError):
        Profile(**kwargs)


def test_sync_engine_uses_freshest_loudness_without_mutating_spectrum_publication(monkeypatch):
    from test_band_normalise import pipeline, publish
    cap = pipeline()
    record = publish(cap, [.2]*10, 960)
    cap._publish_by_interval(
        epoch_id="test", spectrum_updates=[], other_updates=[ProcessorUpdate(
            "loudness_analyzer", 0, 480, loudness_momentary_lufs=-11,
        )], clamp_end=None,
    )
    engine = SyncEngine(None, Profile(energy_source="loudness_fixed"), analyser=cap)

    async def stop_after_tick(_):
        raise asyncio.CancelledError

    monkeypatch.setattr("huesync.sync_engine.asyncio.sleep", stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(engine.run(MagicMock()))
    assert engine.last_energy_input == pytest.approx(19/22)
    assert record.features.loudness_momentary_lufs is None
    assert record.features.full == .2
