import pytest

from huesync.models import Profile
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
    profile = Profile(effect="spectrum_rgb", bars=30)
    effect = ColourModeEffect(profile)
    scene = effect.render(_make_features(), 0.0)
    colour = scene.color_at(_ORIGIN, 0.0)
    assert 0.0 <= colour.r <= 1.0
    assert 0.0 <= colour.g <= 1.0
    assert 0.0 <= colour.b <= 1.0


def test_colour_mode_effect_mono_pulse_equal_rgb():
    """MONO_PULSE must produce equal R/G/B (white-light brightness control)."""
    profile = Profile(effect="mono_pulse", bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([200 / 255.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r == colour.g == colour.b


def test_colour_mode_effect_respects_brightness_floor_on_silence():
    """Silence with brightness_floor=0.2 → every component >= 0.2."""
    profile = Profile(effect="mono_pulse", brightness_floor=0.2, bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([0.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r >= 0.2
    assert colour.g >= 0.2
    assert colour.b >= 0.2


def test_colour_mode_effect_spectrum_rgb_full_treble():
    """Pure treble energy → blue channel active; bass and mid channels are zero."""
    profile = Profile(effect="spectrum_rgb", sensitivity=1.0, bars=30)
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
    profile_low = Profile(effect="mono_pulse", sensitivity=0.5, bars=30)
    profile_high = Profile(effect="mono_pulse", sensitivity=2.0, bars=30)
    low = ColourModeEffect(profile_low).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    high = ColourModeEffect(profile_high).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    assert high.r >= low.r


def test_onset_flash_intensity_brightens_on_onset():
    """onset_flash_intensity > 0 must produce brighter output when onset=True.

    The flash lerps from the rendered colour toward white: c_out = c + fi*(1-c).
    A non-zero intensity with onset=True must be strictly brighter than intensity=0.
    """
    bars = [0.2] * 30
    features_onset = _make_features(bars)
    features_onset.onset = True

    profile_no_flash = Profile(
        effect="spectrum_rgb", onset_flash_intensity=0.0, bars=30
    )
    profile_flash = Profile(
        effect="spectrum_rgb", onset_flash_intensity=0.5, bars=30
    )

    no_flash = ColourModeEffect(profile_no_flash).render(features_onset, 0.0).color_at(_ORIGIN, 0.0)
    with_flash = ColourModeEffect(profile_flash).render(features_onset, 0.0).color_at(_ORIGIN, 0.0)

    assert with_flash.r > no_flash.r
    assert with_flash.g > no_flash.g
    assert with_flash.b > no_flash.b
    assert with_flash.r <= 1.0
    assert with_flash.g <= 1.0
    assert with_flash.b <= 1.0


def test_onset_flash_intensity_no_effect_without_onset():
    """onset_flash_intensity must be ignored when onset=False."""
    bars = [0.3] * 30
    features_no_onset = _make_features(bars)
    # onset defaults to False in AudioFeatures

    profile_no_flash = Profile(
        effect="mono_pulse", onset_flash_intensity=0.0, bars=30
    )
    profile_flash = Profile(
        effect="mono_pulse", onset_flash_intensity=0.8, bars=30
    )

    no_flash = ColourModeEffect(profile_no_flash).render(
        features_no_onset, 0.0
    ).color_at(_ORIGIN, 0.0)
    with_flash = ColourModeEffect(profile_flash).render(
        features_no_onset, 0.0
    ).color_at(_ORIGIN, 0.0)

    assert with_flash.r == no_flash.r
    assert with_flash.g == no_flash.g
    assert with_flash.b == no_flash.b


def test_onset_flash_intensity_full_white_at_1():
    """onset_flash_intensity=1.0 with onset=True must produce RGB=(1,1,1)."""
    bars = [0.1] * 30  # dark input
    features_onset = _make_features(bars)
    features_onset.onset = True

    profile = Profile(
        effect="spectrum_rgb", onset_flash_intensity=1.0, sensitivity=1.0, bars=30
    )
    colour = ColourModeEffect(profile).render(features_onset, 0.0).color_at(_ORIGIN, 0.0)

    assert colour.r == pytest.approx(1.0)
    assert colour.g == pytest.approx(1.0)
    assert colour.b == pytest.approx(1.0)


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
    profile_default = Profile(effect="spectrum_rgb", bars=30)
    colour_default = (
        ColourModeEffect(profile_default).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    )

    # With a very low bass_hz (e.g. 60 Hz) bars 5–9 move into mid → G > 0, R small.
    profile_low_bass = Profile(effect="spectrum_rgb", bars=30, bass_hz=60)
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
        effect="spectrum_rgb",
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
    profile_a = Profile(effect="spectrum_rgb", bars=30, bass_hz=250)
    engine = SyncEngine(fifo, profile_a)

    # A bar pattern where bars 5–9 are active (in bass band for default bass_hz).
    bars = [0.0] * 30
    for i in range(5, 10):
        bars[i] = 1.0
    features = _make_features(bars)

    effect = engine._effect  # type: ignore[union-attr]
    colour_before = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)

    # Update to a very low bass_hz so those bars shift into mid (green).
    profile_b = Profile(effect="spectrum_rgb", bars=30, bass_hz=60)
    engine.update_profile(profile_b)

    new_effect = engine._effect  # type: ignore[union-attr]
    colour_after = new_effect.render(features, 0.0).color_at(_ORIGIN, 0.0)

    assert colour_before.r > colour_after.r, "After update_profile, red should decrease"
    assert colour_after.g > colour_before.g, "After update_profile, green should increase"


# ---------------------------------------------------------------------------
# SyncEngine.update_onset_pipeline and update_render
# ---------------------------------------------------------------------------


def test_update_onset_pipeline_clears_state():
    """update_onset_pipeline() resets all onset flags regardless of shm source."""
    from huesync.sync_engine import SyncEngine

    fifo = "/tmp/_nonexistent_fifo_for_test_pipeline"
    profile = Profile(onset_method="combined")
    engine = SyncEngine(fifo, profile)
    # Manually set flags as if a detection had fired.
    engine._last_pcm_onset = True
    engine._last_onset_bass = True
    engine._last_onset_mid = True
    engine._last_onset_treble = True

    engine.update_onset_pipeline(profile)

    assert not engine._last_pcm_onset
    assert not engine._last_onset_bass
    assert not engine._last_onset_mid
    assert not engine._last_onset_treble


def test_update_onset_pipeline_without_shm_nulls_pipelines():
    """Without a SHM source attached, all PCM pipelines are None after update."""
    from huesync.sync_engine import SyncEngine

    fifo = "/tmp/_nonexistent_fifo_for_test_pipeline2"
    engine = SyncEngine(fifo, Profile(onset_method="combined"))

    engine.update_onset_pipeline(Profile(onset_method="multiband"))
    assert engine._pcm_onset is None
    assert engine._pcm_multiband is None
    assert engine._pcm_superflux is None


def test_update_render_updates_exertion_clip():
    """update_render() must propagate the new exertion_clip to BandNormaliser."""
    from huesync.sync_engine import CavaAnalyser, SyncEngine

    fifo = "/tmp/_nonexistent_fifo_for_test_render"
    profile = Profile(exertion_clip=3.0)
    engine = SyncEngine(fifo, profile)

    new_profile = Profile(exertion_clip=2.0)
    engine.update_render(new_profile)

    assert isinstance(engine._analyser, CavaAnalyser)
    assert engine._analyser.normaliser.exertion_clip == pytest.approx(2.0)


def test_update_render_replaces_effect():
    """update_render() must create a new LayerMixer with the updated profile."""
    from huesync.sync_engine import LayerMixer, SyncEngine

    fifo = "/tmp/_nonexistent_fifo_for_test_render2"
    profile = Profile(sensitivity=1.0)
    engine = SyncEngine(fifo, profile)
    old_effect = engine._effect

    new_profile = Profile(sensitivity=2.0)
    engine.update_render(new_profile)

    assert engine._effect is not old_effect
    assert isinstance(engine._effect, LayerMixer)
    # Active layer's profile carries the updated sensitivity.
    assert engine._effect._active.profile.sensitivity == pytest.approx(2.0)


def test_band_normaliser_update_exertion_clip_preserves_ema():
    """update_exertion_clip() changes the clip ratio without touching EMA state."""
    from huesync.sync_engine import BandNormaliser

    norm = BandNormaliser(exertion_clip=3.0)
    # Warm up EMA with a frame so _ema is no longer None.
    frame = bytes([100, 100, 100, 100])
    norm.normalise(frame)
    ema_before = list(norm._ema)  # type: ignore[arg-type]

    norm.update_exertion_clip(2.0)

    assert norm.exertion_clip == pytest.approx(2.0)
    assert norm._ema == ema_before  # EMA unchanged


# ---------------------------------------------------------------------------
# LayerMixer crossfade tests
# ---------------------------------------------------------------------------


def test_layer_mixer_low_energy_stays_mellow():
    """At energy=0.0, mix stays near 0 (pure mellow output)."""
    from huesync.sync_engine import LayerMixer

    active_profile = Profile(
        effect="mono_pulse",  # active layer → grey
        mix_low_threshold=0.3,
        mix_high_threshold=0.7,
        mix_ema_alpha=1.0,  # EMA alpha=1 → instantaneous, no lag
    )
    mellow_profile = Profile(
        effect="spectrum_rgb",  # mellow layer → colours
        mix_low_threshold=0.3,
        mix_high_threshold=0.7,
        mix_ema_alpha=1.0,
    )
    mixer = LayerMixer(active_profile, mellow_profile)

    # Render 5 frames of silence (full=0.0).
    for _ in range(5):
        features = _make_features([0.0] * 30)
        mixer.render(features, 0.0)

    assert mixer.mix < 0.01, f"Expected mix near 0, got {mixer.mix}"


def test_layer_mixer_high_energy_drives_active():
    """At energy=1.0, mix converges toward 1 (pure active output)."""
    from huesync.sync_engine import LayerMixer

    active_profile = Profile(
        effect="mono_pulse",
        mix_low_threshold=0.3,
        mix_high_threshold=0.7,
        mix_ema_alpha=1.0,  # instantaneous
    )
    mellow_profile = Profile(
        effect="spectrum_rgb",
        mix_low_threshold=0.3,
        mix_high_threshold=0.7,
        mix_ema_alpha=1.0,
    )
    mixer = LayerMixer(active_profile, mellow_profile)

    # All bars at maximum → full=1.0 → smoothstep target=1.0.
    for _ in range(5):
        features = _make_features([1.0] * 30)
        mixer.render(features, 0.0)

    assert mixer.mix > 0.99, f"Expected mix near 1, got {mixer.mix}"


def test_layer_mixer_smooth_transition_no_jumps():
    """A gradual energy ramp must produce a monotonically non-decreasing mix."""
    from huesync.sync_engine import LayerMixer

    active_profile = Profile(
        effect="mono_pulse",
        mix_low_threshold=0.2,
        mix_high_threshold=0.8,
        mix_ema_alpha=0.3,
    )
    mellow_profile = Profile(
        effect="spectrum_rgb",
        mix_low_threshold=0.2,
        mix_high_threshold=0.8,
        mix_ema_alpha=0.3,
    )
    mixer = LayerMixer(active_profile, mellow_profile)

    prev_mix = 0.0
    n = 60
    for step in range(n + 1):
        energy = step / n   # ramps from 0.0 to 1.0
        bars = [energy] * 30
        features = _make_features(bars)
        mixer.render(features, float(step))
        # Mix must not drop while energy is rising.
        assert mixer.mix >= prev_mix - 1e-9, (
            f"Mix decreased at step {step}: {prev_mix:.4f} → {mixer.mix:.4f}"
        )
        prev_mix = mixer.mix


def test_layer_mixer_output_is_lerp_of_layers():
    """At mix=0.5 (instantaneous EMA), output colour is the midpoint of both layers."""
    from huesync.sync_engine import ColourModeEffect, LayerMixer

    # Construct profiles where smoothstep(energy=0.5, lo=0.5, hi=0.5) lands mid-range.
    # Check that the mixer output is between what the two layers produce individually.
    energy = 0.5
    bars = [energy] * 30
    features = _make_features(bars)

    active_profile = Profile(
        effect="mono_pulse",
        mix_low_threshold=energy,
        mix_high_threshold=energy,  # smoothstep at exactly lo=hi → 0.5 clamp
        mix_ema_alpha=1.0,
        sensitivity=1.0,
        brightness_floor=0.0,
    )
    mellow_profile = Profile(
        effect="spectrum_rgb",
        mix_low_threshold=energy,
        mix_high_threshold=energy,
        mix_ema_alpha=1.0,
        sensitivity=1.0,
        brightness_floor=0.0,
    )
    mixer = LayerMixer(active_profile, mellow_profile)
    scene = mixer.render(features, 0.0)
    colour_out = scene.color_at(_ORIGIN, 0.0)

    mellow_colour = ColourModeEffect(mellow_profile).render(features, 0.0).color_at(_ORIGIN, 0.0)
    active_colour = ColourModeEffect(active_profile).render(features, 0.0).color_at(_ORIGIN, 0.0)

    # All three channels must be between their mellow and active values.
    for attr in ("r", "g", "b"):
        out = getattr(colour_out, attr)
        lo_val = min(getattr(mellow_colour, attr), getattr(active_colour, attr))
        hi_val = max(getattr(mellow_colour, attr), getattr(active_colour, attr))
        assert lo_val - 1e-6 <= out <= hi_val + 1e-6, (
            f"Channel {attr}: {out:.4f} not in [{lo_val:.4f}, {hi_val:.4f}]"
        )


def test_sync_engine_publishes_last_mix():
    """SyncEngine.last_mix must be 0.0 on construction (before any frames)."""
    from huesync.sync_engine import SyncEngine

    engine = SyncEngine("/tmp/_nonexistent_fifo_last_mix", Profile())
    assert engine.last_mix == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Effect catalogue tests
# ---------------------------------------------------------------------------


def _ef(onset: bool = False, full: float = 0.5, centroid: float = 0.5) -> AudioFeatures:
    return AudioFeatures(
        bars=[0.5] * 30, bass=full * 0.8, mid=full * 0.9,
        full=full, centroid=centroid,
        onset=onset, onset_strength=1.0 if onset else 0.0,
    )


def _prof(**kw) -> Profile:
    return Profile(
        effect=kw.get("effect", "spectrum_rgb"),
        effect_speed=kw.get("effect_speed", 1.0),
        effect_decay=kw.get("effect_decay", 0.3),
        sensitivity=kw.get("sensitivity", 1.0),
        brightness_floor=0.0,
    )


def test_none_effect_is_black():
    """None effect returns Colour.BLACK regardless of input (even onset=True, full=1.0)."""
    from huesync.sync_engine import ColourModeEffect
    from huesync.types import Colour

    effect = ColourModeEffect(_prof(effect="none"))
    for onset in (False, True):
        scene = effect.render(_ef(onset=onset, full=1.0), 0.0)
        colour = scene.color_at(_ORIGIN, 0.0)
        assert colour == Colour.BLACK, f"Expected black, got {colour} (onset={onset})"


def test_pulses_spikes_on_onset():
    """Brightness after onset frame is significantly brighter than before."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="pulses", effect_decay=0.3))
    before = effect.render(_ef(onset=False, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
    after = effect.render(_ef(onset=True, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
    assert after.r > before.r + 0.5, (
        f"Pulses: onset must spike brightness by >0.5, before={before.r:.3f}, after={after.r:.3f}"
    )


def test_pulses_decays_after_onset():
    """After an onset, brightness decreases monotonically over 10 silence frames."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="pulses", effect_decay=0.3))
    effect.render(_ef(onset=True, full=0.0), 0.0)  # trigger onset
    prev_brightness = 1.0
    for _ in range(10):
        colour = effect.render(_ef(onset=False, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
        assert colour.r <= prev_brightness + 1e-9, (
            f"Pulses: brightness must decay monotonically, "
            f"got {colour.r:.4f} after {prev_brightness:.4f}"
        )
        prev_brightness = colour.r


def test_flashes_dark_without_onset():
    """After 15 no-onset frames, brightness falls below 0.05."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="flashes", effect_decay=0.5))
    for _ in range(15):
        colour = effect.render(_ef(onset=False, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r < 0.05, f"Flashes: expected dark after 15 silent frames, got {colour.r:.4f}"


def test_flashes_bright_on_onset():
    """Onset frame produces brightness significantly above the floor."""
    from huesync.sync_engine import ColourModeEffect

    # Use very small decay so the envelope stays high after onset.
    effect = ColourModeEffect(_prof(effect="flashes", effect_decay=0.01))
    scene = effect.render(_ef(onset=True, full=0.5), 0.0)
    colour = scene.color_at(_ORIGIN, 0.0)
    assert colour.r > 0.8, f"Flashes: onset must produce brightness >0.8, got {colour.r:.4f}"


def test_splotches_differs_by_position():
    """After onset, positions with different sin(x*3.7+seed*1.1) signs must differ.

    With seed=1 (post first onset): sin(0.0*3.7+1.1)≈+0.89, sin(-0.5*3.7+1.1)≈-0.68.
    The positive position gets envelope*sensitivity; the negative position gets floor=0.
    """
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="splotches", effect_decay=0.1, sensitivity=1.5))
    scene = effect.render(_ef(onset=True, full=0.5), 0.0)
    pos_bright = Position(0.0, 0.0, 0.0)   # sin > 0 with seed=1
    pos_dark = Position(-0.5, 0.0, 0.0)    # sin < 0 with seed=1
    colour_bright = scene.color_at(pos_bright, 0.0)
    colour_dark = scene.color_at(pos_dark, 0.0)
    assert colour_bright != colour_dark, (
        f"Splotches: positions (0.0) and (-0.5) must differ, "
        f"got bright={colour_bright.r:.4f} dark={colour_dark.r:.4f}"
    )


def test_fireworks_radiates_spatially():
    """After onset, scene has spatial variation across 5 spread positions."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="fireworks", effect_speed=1.0))
    # Trigger onset at t=0; sample the scene at a slightly later time so the
    # wavefront has propagated
    effect.render(_ef(onset=True, full=0.5), 0.0)
    scene = effect.render(_ef(onset=False, full=0.0), 0.03)

    positions = [Position(x, 0.0, 0.0) for x in (-0.9, -0.45, 0.0, 0.45, 0.9)]
    brightnesses = [scene.color_at(p, 0.03).r for p in positions]
    # At least one position must differ from the others
    assert max(brightnesses) - min(brightnesses) > 1e-6, (
        f"Fireworks: no spatial variation found across positions, brightnesses={brightnesses}"
    )


def test_swirl_differs_by_position():
    """Swirl scene produces different colours at different x-positions."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="swirl", effect_speed=1.0))
    scene = effect.render(_ef(onset=False, full=0.5), 0.0)
    pos_left = Position(-0.9, 0.0, 0.0)
    pos_right = Position(0.9, 0.0, 0.0)
    colour_left = scene.color_at(pos_left, 0.0)
    colour_right = scene.color_at(pos_right, 0.0)
    assert colour_left != colour_right, (
        f"Swirl: positions must differ, left={colour_left}, right={colour_right}"
    )


def test_wave_differs_by_position():
    """Wave scene produces different brightness at different x-positions."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="wave", effect_speed=1.0))
    scene = effect.render(_ef(onset=False, full=0.5), 1.0)
    pos_left = Position(-0.9, 0.0, 0.0)
    pos_right = Position(0.9, 0.0, 0.0)
    colour_left = scene.color_at(pos_left, 1.0)
    colour_right = scene.color_at(pos_right, 1.0)
    assert colour_left != colour_right, (
        f"Wave: positions must differ, left={colour_left}, right={colour_right}"
    )


def test_solid_uniform_across_positions():
    """Solid effect returns the same colour at all positions."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="solid"))
    scene = effect.render(_ef(onset=False, full=0.5, centroid=0.5), 0.0)
    positions = [Position(x, 0.0, 0.0) for x in (-0.9, -0.45, 0.0, 0.45, 0.9)]
    colours = [scene.color_at(p, 0.0) for p in positions]
    for c in colours[1:]:
        assert c == colours[0], f"Solid: all positions must be equal, got {colours[0]} vs {c}"


def test_all_active_effects_non_black_on_onset():
    """spectrum_rgb, mono_pulse, pulses, flashes each produce non-black on onset."""
    from huesync.sync_engine import ColourModeEffect
    from huesync.types import Colour

    effects_to_test = ["spectrum_rgb", "mono_pulse", "pulses", "flashes"]
    features = _ef(onset=True, full=0.5)
    for effect_id in effects_to_test:
        effect = ColourModeEffect(_prof(effect=effect_id))
        colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
        assert colour != Colour.BLACK, (
            f"Effect '{effect_id}' returned black on onset frame with full=0.5"
        )


def test_pulses_colour_follows_spectrum():
    """Pulses uses spectrum colour direction, not fixed white.

    Bass-heavy bars (first 8 high, rest low) must give red-dominant output
    because bass maps to the R channel in the spectrum colour split.
    """
    from huesync.sync_engine import ColourModeEffect

    bars = [1.0] * 8 + [0.1] * 22  # heavy bass, quiet mids and treble
    features = AudioFeatures(
        bars=bars, bass=0.9, mid=0.2, full=0.5, centroid=0.1,
        onset=True, onset_strength=1.0,
    )
    effect = ColourModeEffect(_prof(effect="pulses"))
    c = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert c.r > c.g + 0.1, (
        f"Pulses: bass-heavy input must give red-dominant output; "
        f"r={c.r:.3f} g={c.g:.3f} b={c.b:.3f}"
    )
    assert c.r > c.b + 0.1, (
        f"Pulses: bass-heavy input must dominate blue; r={c.r:.3f} b={c.b:.3f}"
    )


def test_fireworks_dark_before_any_onset():
    """Fireworks shows no ambient glow before the first onset."""
    from huesync.sync_engine import ColourModeEffect
    from huesync.types import Colour

    effect = ColourModeEffect(_prof(effect="fireworks"))
    scene = effect.render(_ef(onset=False, full=0.5), t=5.0)
    for pos in (Position(-0.9, 0.0, 0.0), _ORIGIN, Position(0.9, 0.0, 0.0)):
        c = scene.color_at(pos, 5.0)
        assert c == Colour.BLACK, (
            f"Fireworks: expected black before any onset, got {c} at x={pos.x}"
        )


def test_fireworks_burst_and_decay():
    """Fireworks: peak brightness right after onset, clear decay over subsequent frames."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="fireworks", effect_speed=1.0, effect_decay=0.3))
    positions = [Position(x, 0.0, 0.0) for x in (-0.9, -0.45, 0.0, 0.45, 0.9)]

    # onset at t=0; origin_x = sin(0 * 127) * 0.9 = 0.0
    effect.render(_ef(onset=True, full=0.5), t=0.0)

    def peak_r(t_val: float) -> float:
        scene = effect.render(_ef(onset=False, full=0.0), t=t_val)
        return max(scene.color_at(p, t_val).r for p in positions)

    b_01 = peak_r(0.1)   # fresh burst — wavefront near origin
    b_10 = peak_r(1.0)   # 1 s later — wavefront at 0.8, envelope decayed
    b_25 = peak_r(2.5)   # 2.5 s — wavefront past all lights, essentially dark

    assert b_01 > 0.5, f"Fireworks: expected bright burst at t=0.1, got {b_01:.4f}"
    assert b_10 < b_01, (
        f"Fireworks: expected decay from {b_01:.4f} by t=1.0, got {b_10:.4f}"
    )
    assert b_25 < b_10, (
        f"Fireworks: expected further decay from {b_10:.4f} by t=2.5, got {b_25:.4f}"
    )


def test_fireworks_bright_at_onset_regardless_of_position():
    """With the uniform flash, any light position shows a strong burst at onset.

    Worst case: origin at x=0.9, light at x=-0.9 (max separation).
    Without flash the light would have to wait for a particle to travel 1.8 units,
    but the flash ensures full brightness immediately.
    """
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="fireworks", effect_speed=1.0, effect_decay=0.3))
    # Trigger onset at t=0; sin(0)*0.9=0.0 but we fabricate onset at a known non-zero time
    # so that origin_x = sin(1.0 * 127.0) * 0.9 ≈ sin(127) * 0.9 (some non-zero value).
    # Instead sample at t=0 where origin=0 to guarantee origin_x=0.0, then check a far light.
    effect.render(_ef(onset=True, full=0.5), t=0.0)  # origin_x = sin(0)*0.9 = 0.0

    # Immediately after onset, even a light at x=0.9 (far from origin) must be bright.
    scene = effect.render(_ef(onset=False, full=0.0), t=0.0)
    far_light = Position(0.9, 0.0, 0.0)
    c = scene.color_at(far_light, 0.0)
    assert c.r > 0.7, (
        f"Fireworks: far light must show bright flash at onset (got r={c.r:.3f}); "
        "flash component should give immediate impact to all lights"
    )


def test_pulses_attack_is_not_instant():
    """Onset rise must be an exponential approach, not an instant 0→1 spike."""
    from huesync.sync_engine import ColourModeEffect

    effect = ColourModeEffect(_prof(effect="pulses", effect_decay=0.3))
    before = effect.render(_ef(onset=False, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
    after = effect.render(_ef(onset=True, full=0.0), 0.0).color_at(_ORIGIN, 0.0)
    delta = after.r - before.r
    assert delta < 1.0 - 1e-6, (
        f"Pulses: onset must not be an instant 0→1 jump; got delta={delta:.4f}"
    )
    assert delta > 0.3, (
        f"Pulses: onset attack must make visible progress in one frame; got delta={delta:.4f}"
    )


def test_fireworks_colour_follows_spectrum():
    """Fireworks captures spectrum hue at onset; bass-heavy bars give red-dominant burst."""
    from huesync.sync_engine import ColourModeEffect
    from huesync.types import AudioFeatures

    bars = [1.0] * 8 + [0.1] * 22  # heavy bass, quiet mids and treble
    features = AudioFeatures(
        bars=bars, bass=0.9, mid=0.2, full=0.5, centroid=0.1,
        onset=True, onset_strength=1.0,
    )
    effect = ColourModeEffect(_prof(effect="fireworks", effect_speed=1.0))
    # onset at t=0; origin_x = sin(0) * 0.9 = 0.0; particles near x=0.0 at t=0.05
    effect.render(features, t=0.0)
    scene = effect.render(_ef(onset=False, full=0.0), t=0.05)
    c = scene.color_at(Position(0.0, 0.0, 0.0), 0.05)
    assert c.r > c.g + 0.1, (
        f"Fireworks: bass-heavy onset must give red-dominant output; "
        f"r={c.r:.3f} g={c.g:.3f} b={c.b:.3f}"
    )
    assert c.r > c.b + 0.1, (
        f"Fireworks: bass-heavy onset must dominate blue; r={c.r:.3f} b={c.b:.3f}"
    )
