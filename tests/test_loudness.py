"""Standalone published-coefficient compliance and canonical integration."""

from dataclasses import replace

import numpy as np
import pytest
from test_analysis_architecture import _make_cap, _make_frame

from huesync.loudness_analyzer import KWeightedLoudnessAnalyzer
from huesync.loudness_meter import SAMPLE_RATE, SILENCE_LUFS, _self_check
from huesync.models import Profile
from huesync.spectrum_engine import SharedAnalysisFrame
from huesync.sync_engine import StereoMagStft, SyncEngine


def test_loudness_ebu_reference_landmarks_and_silence(capsys):
    # Executes ALL original assertions: EBU momentary and short-term ±0.1 LU,
    # +0.691 dB at 1 kHz, ~4 dB shelf, RLB attenuation, errstate-safe silence.
    _self_check()
    assert "OK  momentary=-22.993 LUFS" in capsys.readouterr().out


def _signal(n):
    sine = 10 ** (-23 / 20) * np.sin(2 * np.pi * 1000 * np.arange(n) / SAMPLE_RATE)
    return np.column_stack((sine, sine)).astype(np.float32)


def _shared(pcm):
    starts = list(range(0, len(pcm) - 2048 + 1, 480))
    return SharedAnalysisFrame(pcm, [], 'test', 0, len(pcm), starts)


def test_loudness_processor_populates_both_windows_and_exact_hops():
    proc = KWeightedLoudnessAnalyzer()
    frame = _shared(_signal(3 * SAMPLE_RATE + 2048))
    updates = proc.feed(frame)
    assert len(updates) == len(frame.hop_sample_starts)
    assert [(u.sample_start, u.sample_end) for u in updates] == [
        (s, s + 480) for s in frame.hop_sample_starts
    ]
    assert updates[0].loudness_momentary_lufs is None
    assert updates[0].loudness_short_term_lufs is None
    assert updates[-1].loudness_momentary_lufs == pytest.approx(-23, abs=.1)
    assert updates[-1].loudness_short_term_lufs == pytest.approx(-23, abs=.1)
    assert updates[-1].processor_id == 'loudness_analyzer'
    assert updates[-1].bars is None and updates[-1].onset is None
    assert proc.flush() == []
    proc.close()


def test_loudness_reset_clears_filters_and_both_windows():
    proc = KWeightedLoudnessAnalyzer()
    proc.feed(_shared(_signal(3 * SAMPLE_RATE + 2048)))
    proc.reset()
    with np.errstate(all='raise'):
        updates = proc.feed(_shared(np.zeros((3 * SAMPLE_RATE + 2048, 2), np.float32)))
    assert updates[0].loudness_momentary_lufs is None
    assert updates[0].loudness_short_term_lufs is None
    assert updates[-1].loudness_momentary_lufs == SILENCE_LUFS
    assert updates[-1].loudness_short_term_lufs == SILENCE_LUFS


def test_loudness_chunk_independent_with_real_shared_stft_clock():
    pcm = _signal(24000)
    def run(chunk):
        proc = KWeightedLoudnessAnalyzer()
        stft = StereoMagStft(SAMPLE_RATE)
        hop_count = 0
        result = []
        for start in range(0, len(pcm), chunk):
            samples = pcm[start:start + chunk]
            mags = stft.push(samples)
            hops = [(hop_count + i) * 480 for i in range(len(mags))]
            hop_count += len(mags)
            result.extend(proc.feed(SharedAnalysisFrame(
                samples, mags, 'test', start, start + len(samples), hops,
            )))
        return result
    reference = run(len(pcm))
    for chunk in (137, 480, 3000):
        actual = run(chunk)
        assert [(u.sample_start, u.sample_end) for u in actual] == [
            (u.sample_start, u.sample_end) for u in reference
        ]
        for a, b in zip(actual, reference, strict=True):
            assert a.loudness_momentary_lufs == pytest.approx(b.loudness_momentary_lufs)


def test_loudness_publication_preview_eos_and_epoch_reset():
    cap = _make_cap()
    engine = SyncEngine(None, Profile(), analyser=cap)
    try:
        records = cap.feed(replace(_make_frame(n=24000), samples=_signal(24000)))
        record = records[-1]
        assert 'loudness_analyzer' in record.effective_processor_ids
        assert record.features.loudness_momentary_lufs == pytest.approx(-23, abs=.1)
        assert record.features.sustained_energy is None
        assert engine.last_loudness == cap.latest_loudness()
        # A newer CAVA-like spectrum interval must not erase the meter preview
        # or be replaced by a delayed loudness record in the Effects snapshot.
        from huesync.spectrum_engine import ProcessorUpdate
        newer = cap._publish_by_interval(
            epoch_id='ep-1',
            spectrum_updates=[ProcessorUpdate('cavacore', 24000, 24480, bars=[.9])],
            other_updates=[], clamp_end=None,
        )[0]
        cap._publish_by_interval(
            epoch_id='ep-1', spectrum_updates=[], other_updates=[ProcessorUpdate(
                'loudness_analyzer', 22080, 22560, loudness_momentary_lufs=-18.4,
            )], clamp_end=None,
        )
        assert cap.latest() is newer.features
        assert engine.last_loudness[0] == -18.4
        cap.end_of_stream()
        assert engine.last_loudness[0] == -18.4
        cap.feed(_make_frame(n=480, epoch_id='new'))
        assert engine.last_loudness == (None, None)
    finally:
        engine.stop()


def test_loudness_real_cava_scheduler_preview_and_silence():
    from test_latency_retirement import _real_cava_scheduler

    cap = _make_cap(engine=_real_cava_scheduler())
    engine = SyncEngine(None, Profile(), analyser=cap)
    pcm = _signal(24000)
    try:
        for pos in range(0, len(pcm), 480):
            cap.feed(replace(_make_frame(sample_pos=pos), samples=pcm[pos:pos + 480]))
        assert engine.last_loudness[0] == pytest.approx(-23, abs=.1)
        assert cap.latest().bars == pytest.approx([.2])
        assert cap._latest_pub.sample_end == len(pcm)
        assert cap._latest_loudness_pub.sample_end < cap._latest_pub.sample_end
        # Long digital silence clears the preview instead of retaining a finite value.
        for pos in range(len(pcm), len(pcm) + 48000, 480):
            cap.feed(_make_frame(sample_pos=pos))
        assert engine.last_loudness[0] == SILENCE_LUFS
    finally:
        engine.stop()
