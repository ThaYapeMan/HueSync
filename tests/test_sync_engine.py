import pytest

from huesync.models import ColorMode, Profile
from huesync.sync_engine import (
    BandNormaliser,
    ColourModeEffect,
    OnsetDetector,
    SyncEngine,
    _band_average,
    _band_avg,
    _hz_to_frac,
)
from huesync.types import AudioFeatures, Position

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_features(bars: list[float] | None = None) -> AudioFeatures:
    """Build an AudioFeatures object from a list of bar values (0.0-1.0).

    Computes cumulative band slices and spectral centroid from the bars,
    matching what CavaAnalyser produces, so ColourModeEffect tests have
    realistic inputs without needing a real FIFO or cava process.
    """
    if bars is None:
        bars = [0.5] * 30
    n = len(bars)
    total = sum(bars)
    centroid = sum(i * v for i, v in enumerate(bars)) / total / n if total > 1e-9 else 0.0
    bass_n = max(1, int(n * 0.20))
    mid_n = max(1, int(n * 0.55))
    return AudioFeatures(
        bars=bars,
        bass=sum(bars[:bass_n]) / bass_n,
        mid=sum(bars[:mid_n]) / mid_n,
        full=sum(bars) / n,
        centroid=centroid,
    )


_ORIGIN = Position(0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# _band_average (kept for compatibility; internal helper)
# ---------------------------------------------------------------------------


def test_band_average_silence():
    frame = bytes([0] * 30)
    assert _band_average(frame, 0.0, 1.0) == 0.0


def test_band_average_full_scale():
    frame = bytes([255] * 30)
    assert _band_average(frame, 0.0, 1.0) == 1.0


# ---------------------------------------------------------------------------
# ColourModeEffect — two modes remaining after bass_brightness removal
# ---------------------------------------------------------------------------


def test_colour_mode_effect_returns_valid_scene():
    """render() must return a Scene whose color_at() yields values in [0, 1]."""
    profile = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30)
    effect = ColourModeEffect(profile)
    scene = effect.render(_make_features(), 0.0)
    colour = scene.color_at(_ORIGIN, 0.0)
    assert 0.0 <= colour.r <= 1.0
    assert 0.0 <= colour.g <= 1.0
    assert 0.0 <= colour.b <= 1.0


def test_colour_mode_effect_mono_pulse_equal_rgb():
    """MONO_PULSE must produce equal R/G/B (white-light brightness control)."""
    profile = Profile(color_mode=ColorMode.MONO_PULSE, bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([200 / 255.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r == colour.g == colour.b


def test_colour_mode_effect_respects_brightness_floor_on_silence():
    """Silence with brightness_floor=0.2 → every component >= 0.2."""
    profile = Profile(color_mode=ColorMode.MONO_PULSE, brightness_floor=0.2, bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([0.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r >= 0.2
    assert colour.g >= 0.2
    assert colour.b >= 0.2


def test_colour_mode_effect_spectrum_rgb_full_treble():
    """Pure treble energy → blue channel active; bass and mid channels are zero."""
    profile = Profile(color_mode=ColorMode.SPECTRUM_RGB, sensitivity=1.0, bars=30)
    effect = ColourModeEffect(profile)
    n = 30
    mid_frac = _hz_to_frac(profile.mid_hz, profile.lower_cutoff_freq, profile.higher_cutoff_freq)
    mid_hi = int(mid_frac * n)
    bars = [0.0] * mid_hi + [1.0] * (n - mid_hi)
    colour = effect.render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    assert colour.b > 0.0
    assert colour.r == 0.0
    assert colour.g == 0.0


def test_colour_mode_effect_sensitivity_scales_output():
    """Higher sensitivity → brighter output (up to the 1.0 clip)."""
    bars = [0.3] * 30
    profile_low = Profile(color_mode=ColorMode.MONO_PULSE, sensitivity=0.5, bars=30)
    profile_high = Profile(color_mode=ColorMode.MONO_PULSE, sensitivity=2.0, bars=30)
    low = ColourModeEffect(profile_low).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    high = ColourModeEffect(profile_high).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    assert high.r >= low.r


# ---------------------------------------------------------------------------
# BandNormaliser
# ---------------------------------------------------------------------------


def test_normaliser_silence_gate_returns_dark_frame():
    """A frame whose mean bar is below the gate threshold → all-dark output."""
    norm = BandNormaliser()
    silent = bytes([0] * 30)
    result = norm.normalise(silent)
    assert all(b == 0 for b in result)
    assert len(result) == 30


def test_normaliser_spike_above_warmup_produces_bright_output():
    """After EMA has warmed up to a moderate level, a sudden spike → output
    well above the steady-state byte (≈ 85 with clip=3.0)."""
    norm = BandNormaliser()
    # Warm the EMA to a moderate level well above the silence gate.
    warm = bytes([100] * 30)
    for _ in range(200):
        norm.normalise(warm)
    # A spike to near-full-scale should yield exertion > 1.0 → bytes > 85.
    spike = bytes([220] * 30)
    result = norm.normalise(spike)
    assert all(b > 128 for b in result)


def test_normaliser_steady_state_converges_to_midpoint():
    """After EMA convergence, a frame equal to the running average should
    produce exertion ≈ 1.0.  With default clip=3.0 that encodes as byte ≈ 85
    (= int(1.0 * 255/3.0)), not 128 — the higher clip gives more headroom."""
    norm = BandNormaliser()
    steady = bytes([180] * 30)
    for _ in range(300):
        norm.normalise(steady)
    result = norm.normalise(steady)
    assert all(82 <= b <= 88 for b in result), (
        f"Expected bytes near 85 after convergence (clip=3.0), got: {list(result[:5])}…"
    )


def test_normaliser_ema_still_updates_during_silence():
    """EMA must update even when the silence gate fires, so the baseline
    decays during pauses and the first post-silence frame is not wild."""
    norm = BandNormaliser()
    loud = bytes([200] * 30)
    for _ in range(100):
        norm.normalise(loud)
    ema_after_loud = list(norm._ema)  # type: ignore[union-attr]

    silent = bytes([0] * 30)
    for _ in range(100):
        norm.normalise(silent)
    ema_after_silence = list(norm._ema)  # type: ignore[union-attr]

    # EMA should have decayed toward 0 during silence, not stayed frozen.
    assert all(a < b for a, b in zip(ema_after_silence, ema_after_loud, strict=True))


# ---------------------------------------------------------------------------
# OnsetDetector — Dixon (2006) three-condition peak-picking
# ---------------------------------------------------------------------------


def _warm_up_onset(detector: OnsetDetector, bars: list[float], frames: int = 50) -> None:
    """Feed *frames* frames to get past the warmup period and fill the buffer."""
    for _ in range(frames):
        detector.process(bars)


def _alternating_warm_up(detector: OnsetDetector, n_bars: int = 30, pairs: int = 60) -> None:
    """Alternate between two bar levels so the EMA settles at a non-trivial level.

    Ends on the high bars ([0.6]*n_bars) so the test sequence can start from there.
    """
    lo = [0.3] * n_bars
    hi = [0.6] * n_bars
    for _ in range(pairs):
        detector.process(lo)
        detector.process(hi)


def test_onset_no_trigger_during_warmup():
    """No onset should fire during the warmup period regardless of flux."""
    detector = OnsetDetector(delta=0.0, alpha=0.0)  # maximally sensitive
    bars = [1.0] * 30
    # Warmup is 30 frames; constant bars produce zero flux, so no onset fires.
    for _ in range(31):
        onset, _ = detector.process(bars)
        assert not onset, "Onset fired during warmup"


def test_onset_triggers_on_sudden_spike():
    """After warmup, a large flux spike triggers an onset within w+1 frames."""
    w = OnsetDetector._W
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)
    # Feed a spike from [0.6] to [1.0] followed by w fall-back frames.
    # The spike becomes the candidate w frames later; the falling frames confirm
    # it is a local maximum and give the detector its lookahead.
    spike_bars = [1.0] * n_bars
    hi_bars = [0.6] * n_bars
    frames = [spike_bars] + [hi_bars] * w
    onset_fired = any(detector.process(f)[0] for f in frames)
    assert onset_fired, f"Expected onset within {w + 1} frames of spike"


def test_onset_fires_at_peak_not_rising_edge():
    """Dixon condition 1: onset fires at the flux peak, not on the rising edge.

    A gradual rise followed by a single large jump then a fall must produce
    exactly one onset, timed to the large jump (the true local maximum), not to
    any frame on the rising slope.  alpha=0.0 disables condition 3 so this test
    isolates conditions 1 and 2.
    """
    w = OnsetDetector._W  # 3
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)

    # Bar sequence starting from [0.6] (last warmup level).
    # Flux per step = 30 * max(0, new_level - prev_level).
    bar_sequence = [
        [0.65] * n_bars,  # step 0: flux = 1.5  (rising edge)
        [0.70] * n_bars,  # step 1: flux = 1.5  (rising edge)
        [0.75] * n_bars,  # step 2: flux = 1.5  (rising edge)
        [1.00] * n_bars,  # step 3: flux = 7.5  ← PEAK
        [0.95] * n_bars,  # step 4: flux = 0    (falling)
        [0.80] * n_bars,  # step 5: flux = 0    (falling)
        [0.60] * n_bars,  # step 6: flux = 0    ← onset fires here (peak + w)
        [0.40] * n_bars,  # step 7: flux = 0
        [0.20] * n_bars,  # step 8: flux = 0
    ]

    onsets = [step for step, bars in enumerate(bar_sequence) if detector.process(bars)[0]]

    assert len(onsets) == 1, f"Expected 1 onset, got {len(onsets)} at steps {onsets}"
    assert onsets[0] == 3 + w, (
        f"Expected onset at step {3 + w} (peak index 3 + lookahead w={w}), "
        f"got step {onsets[0]}"
    )


def test_onset_no_trigger_on_slow_rise():
    """A monotonically rising sequence with constant flux has no local maximum.

    Condition 2 (above local mean + delta) also fails because every frame's
    normalised flux is identical — none exceeds the window mean by delta > 0.
    """
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)

    # Linear rise from 0.6 to 1.0 in 20 equal steps: constant flux ≈ 0.6/20 * 30.
    any_onset = False
    for step in range(20):
        level = 0.6 + (step + 1) * 0.4 / 20
        onset, _ = detector.process([level] * n_bars)
        if onset:
            any_onset = True

    assert not any_onset, "Expected no onset on monotonically rising flux"


def test_onset_strength_positive_on_rising_energy():
    """Flux strength must be > 0 when energy rises."""
    detector = OnsetDetector()
    bars_low = [0.1] * 30
    bars_high = [0.9] * 30
    detector.process(bars_low)  # initialise prev
    _, strength = detector.process(bars_high)
    assert strength > 0.0


def test_onset_strength_zero_on_falling_energy():
    """Flux is one-sided (positive differences only), so falling energy → 0."""
    detector = OnsetDetector()
    bars_high = [0.9] * 30
    bars_low = [0.1] * 30
    detector.process(bars_high)
    _, strength = detector.process(bars_low)
    assert strength == 0.0


# ---------------------------------------------------------------------------
# _hz_to_frac and _band_avg
# ---------------------------------------------------------------------------


def test_hz_to_frac_known_values():
    """Verify a few known values of the log-scale mapping."""
    # At the exact lower bound, fraction = 0.0; at upper bound, fraction = 1.0.
    assert _hz_to_frac(50, 50, 12000) == pytest.approx(0.0)
    assert _hz_to_frac(12000, 50, 12000) == pytest.approx(1.0)
    # 2000 Hz is at about 67% of the log range 50–12000.
    frac = _hz_to_frac(2000, 50, 12000)
    assert 0.65 < frac < 0.70
    # Clamping: below lower → 0, above upper → 1.
    assert _hz_to_frac(1, 50, 12000) == pytest.approx(0.0)
    assert _hz_to_frac(99999, 50, 12000) == pytest.approx(1.0)


def test_band_avg_empty_slice():
    """_band_avg returns 0.0 for an empty slice (lo >= hi)."""
    bars = [1.0] * 10
    assert _band_avg(bars, 5, 5) == 0.0
    assert _band_avg(bars, 0, 0) == 0.0


# ---------------------------------------------------------------------------
# Profile cutoff defaults — single source of truth
# ---------------------------------------------------------------------------


def test_profile_cutoff_defaults():
    """Profile defaults for cutoff frequencies must match the frontend Reset targets.

    The Reset button in NowPlaying uses status.lower_cutoff_freq / higher_cutoff_freq,
    which come from the active profile. If the profile was created with all defaults,
    these are the values Reset will land on. Verify them here so a drift between
    backend default and frontend expectation is caught immediately.
    """
    p = Profile()
    assert p.lower_cutoff_freq == 50
    assert p.higher_cutoff_freq == 12000


def test_profile_band_boundary_defaults_within_cutoff_range():
    """bass_hz and mid_hz defaults must lie strictly inside the cutoff range.

    A boundary outside [lower_cutoff_freq, higher_cutoff_freq] produces an
    empty band, which is confusing and usually wrong.
    """
    p = Profile()
    assert p.lower_cutoff_freq < p.bass_hz < p.higher_cutoff_freq
    assert p.bass_hz < p.mid_hz < p.higher_cutoff_freq


# ---------------------------------------------------------------------------
# ColourModeEffect — band boundary shift
# ---------------------------------------------------------------------------


def test_colour_mode_effect_bass_hz_shifts_red_green_boundary():
    """Raising bass_hz moves the red/green split: more bars fall in bass (red)."""
    bars = [0.0] * 30
    # Bars 5–9 are active; below the default bass boundary (250 Hz → ~bar 15 of 30).
    for i in range(5, 10):
        bars[i] = 1.0

    # With default bass_hz (250 Hz) those bars land in the bass band → R > 0.
    profile_default = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30)
    colour_default = (
        ColourModeEffect(profile_default).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    )

    # With a very low bass_hz (e.g. 60 Hz) bars 5–9 move into mid → G > 0, R small.
    profile_low_bass = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30, bass_hz=60)
    colour_low_bass = (
        ColourModeEffect(profile_low_bass).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    )

    assert colour_default.r > colour_low_bass.r, "Lowering bass_hz should reduce red"
    assert colour_low_bass.g > colour_default.g, "Lowering bass_hz should increase green"


def test_colour_mode_effect_onset_signals_do_not_affect_brightness():
    """ColourModeEffect.render() must produce identical output regardless of onset
    signal values in AudioFeatures.

    ColourModeEffect derives brightness solely from features.bars.  The onset
    fields (onset, onset_bass, onset_mid, onset_treble) are stored for the GUI
    indicator and for future stateful effects, but must not alter the rendered
    colour in the current implementation.  If they did, multiband onset_method
    (which sets three onset flags simultaneously) would produce structurally
    brighter output than combined (which does not set any onset flag at all),
    causing a persistent brightness difference that cannot be tuned away.
    """
    profile = Profile(
        color_mode=ColorMode.SPECTRUM_RGB,
        sensitivity=1.0,
        brightness_floor=0.0,
        bars=30,
    )
    effect = ColourModeEffect(profile)
    bars = [0.4] * 30
    n = len(bars)
    total = sum(bars)
    centroid = sum(i * v for i, v in enumerate(bars)) / total / n

    base = AudioFeatures(
        bars=bars,
        bass=bars[0],
        mid=bars[0],
        full=bars[0],
        centroid=centroid,
        onset=False,
        onset_bass=False,
        onset_mid=False,
        onset_treble=False,
        onset_strength=0.0,
        onset_bass_strength=0.0,
        onset_mid_strength=0.0,
        onset_treble_strength=0.0,
    )
    all_onset = AudioFeatures(
        bars=bars,
        bass=bars[0],
        mid=bars[0],
        full=bars[0],
        centroid=centroid,
        onset=True,
        onset_bass=True,
        onset_mid=True,
        onset_treble=True,
        onset_strength=9.9,
        onset_bass_strength=9.9,
        onset_mid_strength=9.9,
        onset_treble_strength=9.9,
    )

    colour_no_onset = effect.render(base, 0.0).color_at(_ORIGIN, 0.0)
    colour_all_onset = effect.render(all_onset, 0.0).color_at(_ORIGIN, 0.0)

    assert colour_no_onset.r == colour_all_onset.r, (
        "onset flags must not affect red channel brightness"
    )
    assert colour_no_onset.g == colour_all_onset.g, (
        "onset flags must not affect green channel brightness"
    )
    assert colour_no_onset.b == colour_all_onset.b, (
        "onset flags must not affect blue channel brightness"
    )


def test_sync_engine_update_profile_takes_effect():
    """update_profile() must replace the ColourModeEffect so that a new bass_hz
    is immediately reflected in the rendered colour without restarting the engine.
    """
    fifo = "/tmp/_nonexistent_fifo_for_test"  # SyncEngine only opens FIFO on start()
    profile_a = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30, bass_hz=250)
    engine = SyncEngine(fifo, profile_a)

    # A bar pattern where bars 5–9 are active (in bass band for default bass_hz).
    bars = [0.0] * 30
    for i in range(5, 10):
        bars[i] = 1.0
    features = _make_features(bars)

    effect = engine._effect  # type: ignore[union-attr]
    colour_before = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)

    # Update to a very low bass_hz so those bars shift into mid (green).
    profile_b = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30, bass_hz=60)
    engine.update_profile(profile_b)

    new_effect = engine._effect  # type: ignore[union-attr]
    colour_after = new_effect.render(features, 0.0).color_at(_ORIGIN, 0.0)

    assert colour_before.r > colour_after.r, "After update_profile, red should decrease"
    assert colour_after.g > colour_before.g, "After update_profile, green should increase"
