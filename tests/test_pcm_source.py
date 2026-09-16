"""Tests for SqueezeliteShmSource (PCM tap, step 1).

All tests use a synthetic vis_t file: a regular tempfile written to match
the shared-memory layout.  The source's mmap and any writable mmap used by
the test both refer to the same file, so writes from the test side are
visible through the source's read-only view.
"""

import logging
import mmap
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from huesync.pcm_source import (
    _BUF_OFFSET,
    _BUF_OFFSET_V1,
    _HDR_FMT,
    _HDR_OFFSET,
    _HDR_SIZE,
    _MMAP_SIZE,
    _MMAP_SIZE_V1,
    _V2_EXT_FMT,
    _V2_EXT_OFFSET,
    _V2_EXT_SIZE,
    SHM_ABI_V1_MAGIC,
    VIS_BUF_SIZE,
    WINDOW_SIZE,
    DataResult,
    PcmHpss,
    PcmStft,
    SqueezeliteShmSource,
    SqueezeliteShmStereoSource,
    StreamInvalidated,
    TemporarilyNoData,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HDR_END = _HDR_OFFSET + _HDR_SIZE  # == _BUF_OFFSET == 80


def _make_vis_t(
    buf_index: int = 0,
    running: bool = True,
    rate: int = 44100,
    buffer: bytes | None = None,
    updated: int = 0,
) -> bytes:
    """Build a complete vis_t image ready to write to a tempfile."""
    lock_bytes = b"\x00" * _HDR_OFFSET
    header = struct.pack(_HDR_FMT, VIS_BUF_SIZE, buf_index, int(running), rate, updated)
    buf_bytes = buffer if buffer is not None else b"\x00" * (VIS_BUF_SIZE * 2)
    return lock_bytes + header + buf_bytes


def _write_vis_t(tmp_path: Path, **kwargs: object) -> Path:
    p = tmp_path / "squeezelite-test"
    p.write_bytes(_make_vis_t(**kwargs))  # type: ignore[arg-type]
    return p


def _open_writable_mm(path: Path) -> mmap.mmap:
    """Return a writable mmap on *path* for test-side mutations."""
    fd = path.open("r+b")
    mm = mmap.mmap(fd.fileno(), _MMAP_SIZE)
    fd.close()
    return mm


def _set_buf_index(mm: mmap.mmap, index: int) -> None:
    mm.seek(_HDR_OFFSET + 4)  # buf_index is 4 bytes after buf_size
    mm.write(struct.pack("<I", index))
    mm.flush()


def _write_samples(mm: mmap.mmap, start: int, s16_values: list[int]) -> None:
    """Write s16 values into the circular buffer, wrapping at VIS_BUF_SIZE."""
    for i, v in enumerate(s16_values):
        pos = (start + i) % VIS_BUF_SIZE
        mm.seek(_BUF_OFFSET + pos * 2)
        mm.write(struct.pack("<h", v))
    mm.flush()


def _set_running(mm: mmap.mmap, running: bool) -> None:
    mm.seek(_HDR_OFFSET + 8)  # running_byte is 8 bytes after buf_size (past buf_size+buf_index)
    mm.write(struct.pack("<B", int(running)))
    mm.flush()


def _set_rate(mm: mmap.mmap, rate: int) -> None:
    mm.seek(_HDR_OFFSET + 12)  # rate follows buf_size + buf_index + running + pad
    mm.write(struct.pack("<I", rate))
    mm.flush()


def _set_updated(mm: mmap.mmap, updated: int) -> None:
    mm.seek(_HDR_OFFSET + 16)  # updated (time_t) follows rate
    mm.write(struct.pack("<q", updated))
    mm.flush()


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def test_sample_rate_read_from_header(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, rate=44100)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.sample_rate == 44100
    src.close()


def test_sample_rate_48k(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, rate=48000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.sample_rate == 48000
    src.close()


def test_running_true(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, running=True)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.running is True
    src.close()


def test_running_false(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, running=False)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.running is False
    src.close()


# ---------------------------------------------------------------------------
# read_new — no new samples
# ---------------------------------------------------------------------------


def test_read_new_empty_on_open(tmp_path: Path) -> None:
    """open() snapshots buf_index; immediate read_new() returns nothing."""
    p = _write_vis_t(tmp_path, buf_index=100)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    result = src.read_new()
    assert result.shape == (0,)
    assert result.dtype == np.float32
    src.close()


# ---------------------------------------------------------------------------
# read_new — simple (no wraparound)
# ---------------------------------------------------------------------------


def test_read_new_basic(tmp_path: Path) -> None:
    """Two stereo frames → two mono samples with correct downmix."""
    # Stereo pairs: frame0 = (L=1000, R=2000), frame1 = (L=3000, R=4000).
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hhhh", buf, 0, 1000, 2000, 3000, 4000)

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0  # rewind so read_new() sees samples 0-3

    result = src.read_new()

    assert result.shape == (2,)
    assert result.dtype == np.float32
    np.testing.assert_allclose(result[0], (1000 + 2000) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[1], (3000 + 4000) / (2.0 * 32768.0), rtol=1e-6)
    src.close()


def test_read_new_scaled_to_unit_range(tmp_path: Path) -> None:
    """Maximum int16 value maps to 1.0; minimum maps to ≈−1.0."""
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 32767, 32767)   # max L, max R
    struct.pack_into("<hh", buf, 4, -32768, -32768)  # min L, min R

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    result = src.read_new()

    assert result.shape == (2,)
    np.testing.assert_allclose(result[0], 32767 / 32768.0, rtol=1e-5)
    np.testing.assert_allclose(result[1], -1.0, rtol=1e-5)
    src.close()


def test_read_new_advances_prev_index(tmp_path: Path) -> None:
    """After read_new(), a second call with no new data returns empty."""
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 100, 200)

    p = _write_vis_t(tmp_path, buf_index=2, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    first = src.read_new()
    assert first.shape == (1,)

    second = src.read_new()
    assert second.shape == (0,)
    src.close()


# ---------------------------------------------------------------------------
# read_new — wraparound
# ---------------------------------------------------------------------------


def test_read_new_wraparound(tmp_path: Path) -> None:
    """Circular-buffer wraparound returns contiguous samples in write order."""
    # Three stereo frames spanning the wrap boundary:
    #   frame_A at s16 positions 16382-16383
    #   frame_B at s16 positions 0-1
    #   frame_C at s16 positions 2-3
    # buf_index after writing = 4 (wrapped around from 16384).
    FRAME_A = (100, 200)
    FRAME_B = (300, 400)
    FRAME_C = (500, 600)

    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 16382 * 2, *FRAME_A)
    struct.pack_into("<hh", buf, 0 * 2, *FRAME_B)
    struct.pack_into("<hh", buf, 2 * 2, *FRAME_C)

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 16382  # last position before the three frames were written

    result = src.read_new()

    # n_new = (4 - 16382) % 16384 = 6 → 3 mono samples
    assert result.shape == (3,)
    np.testing.assert_allclose(result[0], (100 + 200) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[1], (300 + 400) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[2], (500 + 600) / (2.0 * 32768.0), rtol=1e-6)
    src.close()


# ---------------------------------------------------------------------------
# read_new — fell behind
# ---------------------------------------------------------------------------


def test_fell_behind_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """When n_new > VIS_BUF_SIZE // 2, a WARNING is logged."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0  # n_new = 9000 > 8192 → fell behind

    with caplog.at_level(logging.WARNING, logger="huesync.pcm_source"):
        src.read_new()

    assert any("fell behind" in r.message.lower() for r in caplog.records)
    src.close()


def test_fell_behind_returns_newest_window(tmp_path: Path) -> None:
    """Fell-behind case returns exactly VIS_BUF_SIZE // 2 mono samples."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    result = src.read_new()

    # VIS_BUF_SIZE // 2 = 8192 s16 samples = 4096 stereo frames = 4096 mono samples.
    assert result.shape == (VIS_BUF_SIZE // 4,)
    assert result.dtype == np.float32
    src.close()


def test_fell_behind_subsequent_read_is_empty(tmp_path: Path) -> None:
    """After a fell-behind read, prev_index advances to buf_index; next call empty."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    src.read_new()           # consumes the fell-behind window
    result = src.read_new()  # no new data since buf_index didn't move

    assert result.shape == (0,)
    src.close()


# ---------------------------------------------------------------------------
# read_new — seqlock torn-read detection
# ---------------------------------------------------------------------------


def test_torn_read_discards_block(tmp_path: Path) -> None:
    """If buf_index changes between the two seqlock reads, return empty.

    Simulates a concurrent squeezelite write by overriding _read_header() on
    the instance so that its second call (post-copy check) returns a different
    buf_index than its first call (pre-copy snapshot).
    """
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 100, 200)  # one valid stereo frame
    p = _write_vis_t(tmp_path, buf_index=2, buffer=bytes(buf))

    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0  # 2 pending s16 samples → would normally return 1 mono sample

    # Intercept _read_header: the 1st call returns the real value (buf_index=2);
    # the 2nd call (seqlock post-copy check) simulates the writer advancing to 4.
    _real = SqueezeliteShmSource._read_header
    _calls = [0]

    def _patched() -> tuple[int, int, bool, int, int]:
        _calls[0] += 1
        r = _real(src)
        if _calls[0] == 2:
            return (r[0], (r[1] + 2) % VIS_BUF_SIZE, r[2], r[3], r[4])
        return r

    src._read_header = _patched  # type: ignore[method-assign]

    result = src.read_new()

    assert result.shape == (0,), "torn read should be discarded"
    assert result.dtype == np.float32
    # prev_index advanced to buf_index_before so the next poll picks up from there.
    assert src._prev_index == 2
    src.close()


# ---------------------------------------------------------------------------
# Live update via shared mmap (simulates squeezelite writing new frames)
# ---------------------------------------------------------------------------


def test_incremental_reads(tmp_path: Path) -> None:
    """Two successive polls each return only their own new frames."""
    p = _write_vis_t(tmp_path, buf_index=0)

    src = SqueezeliteShmSource()
    src.open("x", _path=p)

    with _open_writable_mm(p) as w_mm:
        # Write 2 stereo frames (4 s16 values) starting at position 0.
        _write_samples(w_mm, 0, [10, 20, 30, 40])
        _set_buf_index(w_mm, 4)

        first = src.read_new()
        assert first.shape == (2,)
        np.testing.assert_allclose(first[0], (10 + 20) / (2.0 * 32768.0), rtol=1e-6)

        # Write 1 more stereo frame at position 4.
        _write_samples(w_mm, 4, [50, 60])
        _set_buf_index(w_mm, 6)

        second = src.read_new()
        assert second.shape == (1,)
        np.testing.assert_allclose(second[0], (50 + 60) / (2.0 * 32768.0), rtol=1e-6)

    src.close()


# ---------------------------------------------------------------------------
# PcmStft
# ---------------------------------------------------------------------------


def test_hop_44100() -> None:
    assert PcmStft(44100).hop == 441


def test_hop_48000() -> None:
    assert PcmStft(48000).hop == 480


def test_n_bins() -> None:
    assert PcmStft(44100).n_bins == 1025


def test_output_shape() -> None:
    stft = PcmStft(44100)
    silence = np.zeros(WINDOW_SIZE, dtype=np.float32)
    frames = stft.push(silence)
    assert len(frames) == 1
    assert frames[0].shape == (1025,)
    assert frames[0].dtype == np.float32


def test_sine_peak_at_correct_bin() -> None:
    """1000 Hz sine @ 44100 Hz must peak at bin round(1000 * 2048 / 44100) == 46."""
    stft = PcmStft(44100)
    t = np.arange(WINDOW_SIZE) / 44100
    sine = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    frames = stft.push(sine)
    assert len(frames) == 1
    assert np.argmax(frames[0]) == round(1000 * WINDOW_SIZE / 44100)


def test_rolling_buffer_small_chunks() -> None:
    """WINDOW_SIZE samples split into 16 chunks of 128 give the same frame as one push."""
    t = np.arange(WINDOW_SIZE) / 44100
    signal = np.sin(2 * np.pi * 440 * t).astype(np.float32)

    stft_one = PcmStft(44100)
    frames_one = stft_one.push(signal)
    assert len(frames_one) == 1

    stft_chunked = PcmStft(44100)
    all_frames: list[np.ndarray] = []
    chunk_size = 128
    for i in range(0, WINDOW_SIZE, chunk_size):
        all_frames.extend(stft_chunked.push(signal[i : i + chunk_size]))

    assert len(all_frames) == 1
    np.testing.assert_array_equal(frames_one[0], all_frames[0])


def test_multiple_frames_per_push() -> None:
    """Pushing 4096 samples yields multiple frames, each with shape (1025,)."""
    stft = PcmStft(44100)
    signal = np.zeros(4096, dtype=np.float32)
    frames = stft.push(signal)
    assert len(frames) > 1
    for frame in frames:
        assert frame.shape == (1025,)
        assert frame.dtype == np.float32


# ---------------------------------------------------------------------------
# PcmHpss
# ---------------------------------------------------------------------------


def _make_harmonic_signal(freq_hz: float = 440.0, n_frames: int = 30) -> np.ndarray:
    """Repeated 440 Hz sine wave long enough for PcmHpss to fill its buffer."""
    sr = 44100
    t = np.arange(WINDOW_SIZE * n_frames) / sr
    return np.sin(2 * np.pi * freq_hz * t).astype(np.float32)


def _make_percussive_signal(n_frames: int = 30) -> np.ndarray:
    """Silence with ONE impulse in the middle — broadband but NOT sustained.

    A periodic impulse train would fool HPSS into calling it harmonic (constant
    across time).  A single impulse surrounded by silence is not time-consistent
    and is correctly identified as percussive.
    """
    n = WINDOW_SIZE * n_frames
    signal = np.zeros(n, dtype=np.float32)
    # Place impulse at the midpoint of the signal.
    signal[n // 2] = 1.0
    return signal


def _push_all(hpss: PcmHpss, signal: np.ndarray) -> list[tuple[float, float]]:
    """Push the full signal through hpss in one batch; return all results."""
    return hpss.push(signal)


def test_hpss_harmonic_signal_dominated_by_harmonic() -> None:
    """A sustained sine wave should produce harmonic_energy >> percussive_energy."""
    hpss = PcmHpss(44100)
    results = _push_all(hpss, _make_harmonic_signal())
    assert len(results) > 0
    # Take the last few results (after buffer warmup)
    tail = results[len(results) // 2:]
    avg_p = sum(p for p, _ in tail) / len(tail)
    avg_h = sum(h for _, h in tail) / len(tail)
    assert avg_h > 0.6, f"harmonic energy unexpectedly low: {avg_h:.3f}"
    assert avg_p < 0.4, f"percussive energy unexpectedly high: {avg_p:.3f}"


def test_hpss_percussive_signal_dominated_by_percussive() -> None:
    """A series of impulses (spectrally flat, not sustained) should be percussive."""
    hpss = PcmHpss(44100)
    results = _push_all(hpss, _make_percussive_signal())
    assert len(results) > 0
    tail = results[len(results) // 2:]
    avg_p = sum(p for p, _ in tail) / len(tail)
    avg_h = sum(h for _, h in tail) / len(tail)
    assert avg_p > avg_h, (
        f"percussive signal not dominated by percussive energy: p={avg_p:.3f} h={avg_h:.3f}"
    )


def test_hpss_energies_sum_to_unity() -> None:
    """percussive_energy + harmonic_energy must be ≈ 1.0 for all frames."""
    hpss = PcmHpss(44100)
    signal = _make_harmonic_signal()
    results = hpss.push(signal)
    for p, h in results:
        assert abs((p + h) - 1.0) < 1e-4, f"energies don't sum to 1: p={p:.4f} h={h:.4f}"


def test_hpss_empty_push_returns_empty() -> None:
    """Pushing fewer than WINDOW_SIZE samples yields no frames."""
    hpss = PcmHpss(44100)
    results = hpss.push(np.zeros(100, dtype=np.float32))
    assert results == []


def test_hpss_small_chunks_match_single_push() -> None:
    """Same harmonic signal pushed in small chunks gives the same tail energy as one push."""
    signal = _make_harmonic_signal(n_frames=25)

    hpss_one = PcmHpss(44100)
    results_one = hpss_one.push(signal)

    hpss_chunked = PcmHpss(44100)
    results_chunked: list[tuple[float, float]] = []
    chunk = 441  # 10 ms at 44100 Hz
    for i in range(0, len(signal), chunk):
        results_chunked.extend(hpss_chunked.push(signal[i : i + chunk]))

    assert len(results_one) == len(results_chunked)
    for (p1, h1), (p2, h2) in zip(results_one, results_chunked, strict=True):
        assert abs(p1 - p2) < 1e-5
        assert abs(h1 - h2) < 1e-5


def test_hpss_output_is_float_pairs() -> None:
    """Each result element is a (float, float) tuple with values in [0, 1]."""
    hpss = PcmHpss(44100)
    results = hpss.push(_make_harmonic_signal(n_frames=5))
    assert len(results) > 0
    for p, h in results:
        assert isinstance(p, float)
        assert isinstance(h, float)
        assert 0.0 <= p <= 1.0
        assert 0.0 <= h <= 1.0


# ---------------------------------------------------------------------------


def _open_stereo_src(path: Path) -> tuple["SqueezeliteShmStereoSource", mmap.mmap]:
    """Open a SqueezeliteShmStereoSource on *path* and return (src, writable_mm).

    These legacy tests exercise the v0 fall-through code path.  Production
    canonical LMS PCM sessions reject v0 with require_v1=True (the default).
    """
    src = SqueezeliteShmStereoSource()
    src.open("test", _path=path, require_v1=False)
    mm = _open_writable_mm(path)
    return src, mm


def test_shm_stereo_ordinary_advance(tmp_path: Path) -> None:
    """Normal forward read returns DataResult with correct shape."""
    p = _write_vis_t(tmp_path, buf_index=0, running=True, rate=44100)
    src, mm = _open_stereo_src(p)
    # Write 8 s16 samples (4 stereo frames: L0,R0,L1,R1,...) starting at index 0.
    samples = [100, -100, 200, -200, 300, -300, 400, -400]
    _write_samples(mm, 0, samples)
    _set_buf_index(mm, 8)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, DataResult)
    assert result.frame.samples.shape == (4, 2)  # 4 stereo frames
    assert result.frame.channels == 2
    assert result.frame.sample_rate == 44100


def test_shm_stereo_no_new_data(tmp_path: Path) -> None:
    """buf_index unchanged, updated unchanged → TemporarilyNoData."""
    p = _write_vis_t(tmp_path, buf_index=10, running=True, rate=44100, updated=1000)
    src, mm = _open_stereo_src(p)
    # Don't advance buf_index or updated.
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, TemporarilyNoData)


def test_shm_stereo_full_lap_aliasing(tmp_path: Path) -> None:
    """raw_delta==0 with updated timestamp advanced → StreamInvalidated (full lap)."""
    p = _write_vis_t(tmp_path, buf_index=100, running=True, rate=44100, updated=1000)
    src, mm = _open_stereo_src(p)
    # Simulate full lap: buf_index stays at 100 (mod VIS_BUF_SIZE), but updated advances.
    _set_updated(mm, 1001)  # writer timestamp advanced without buf_index moving visibly
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_shm_stereo_large_delta_fell_behind(tmp_path: Path) -> None:
    """raw_delta > VIS_BUF_SIZE // 2 → StreamInvalidated (fell too far behind)."""
    p = _write_vis_t(tmp_path, buf_index=0, running=True, rate=44100)
    src, mm = _open_stereo_src(p)
    # Advance by more than half the buffer.
    _set_buf_index(mm, VIS_BUF_SIZE // 2 + 1)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_shm_stereo_wrap_around(tmp_path: Path) -> None:
    """Circular buffer wrap (buf_index wraps to 0) returns correct samples."""
    # Place prev_index near the end of the buffer.
    wrap_start = VIS_BUF_SIZE - 4  # 4 samples before end
    p = _write_vis_t(tmp_path, buf_index=wrap_start, running=True, rate=44100)
    src, mm = _open_stereo_src(p)
    # Write 8 interleaved s16 samples wrapping around (produces 4 stereo frames).
    samples = [10, -10, 20, -20, 30, -30, 40, -40]
    _write_samples(mm, wrap_start, samples)
    new_index = (wrap_start + 8) % VIS_BUF_SIZE
    _set_buf_index(mm, new_index)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, DataResult)
    assert result.frame.samples.shape == (4, 2)


def test_shm_stereo_running_false_to_true(tmp_path: Path) -> None:
    """running transition False→True emits StreamInvalidated (restart detected)."""
    p = _write_vis_t(tmp_path, buf_index=100, running=False, rate=44100)
    src, mm = _open_stereo_src(p)
    # Simulate squeezelite restart: running goes True.
    _set_running(mm, True)
    _set_buf_index(mm, 0)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_shm_stereo_running_false_stays_no_data(tmp_path: Path) -> None:
    """running stays False, buf_index unchanged → TemporarilyNoData (not a restart)."""
    p = _write_vis_t(tmp_path, buf_index=50, running=False, rate=44100)
    src, mm = _open_stereo_src(p)
    # No changes — source is paused.
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, TemporarilyNoData)


def test_shm_stereo_rate_change(tmp_path: Path) -> None:
    """Sample rate change → StreamInvalidated (track or restart at different rate)."""
    p = _write_vis_t(tmp_path, buf_index=0, running=True, rate=44100)
    src, mm = _open_stereo_src(p)
    # Simulate rate change mid-stream.
    _set_rate(mm, 48000)
    _set_buf_index(mm, 200)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_shm_stereo_rate_unchanged_normal_advance(tmp_path: Path) -> None:
    """Rate unchanged: normal advance still produces DataResult."""
    p = _write_vis_t(tmp_path, buf_index=0, running=True, rate=48000)
    src, mm = _open_stereo_src(p)
    _write_samples(mm, 0, [10, -10, 20, -20])  # 2 stereo frames
    _set_buf_index(mm, 4)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, DataResult)
    assert result.frame.sample_rate == 48000


def test_shm_stereo_initial_rate_zero_no_spurious_invalidation(tmp_path: Path) -> None:
    """Rate guard skips check when _prev_rate==0 (first read after open on non-zero rate)."""
    # Source opened at rate=0 would normally trigger the rate-change check.
    # The guard 'self._prev_rate != 0' must prevent a false invalidation on first read.
    p = _write_vis_t(tmp_path, buf_index=0, running=True, rate=44100)
    src, mm = _open_stereo_src(p)
    # _prev_rate was anchored to 44100 on open() — no spurious invalidation expected.
    _write_samples(mm, 0, [100, -100])  # 1 stereo frame
    _set_buf_index(mm, 2)
    result = src.read()
    mm.close()
    src.close()

    assert isinstance(result, DataResult)


# ---------------------------------------------------------------------------
# SqueezeliteShmStereoSource — v1 ABI tests (production path)
# ---------------------------------------------------------------------------


def _write_stereo_shm_v1(
    tmp_path: Path,
    *,
    buf_index: int = 0,
    running: bool = True,
    rate: int = 44100,
    updated: int = 0,
    magic: int = SHM_ABI_V1_MAGIC,
    abi_version: int = 1,
    write_seq: int = 2,
    generation: int = 0xC0FFEE,
    abs_write_pos: int = 0,
    gap_seq: int = 0,
    pcm_at_120: bytes | None = None,
    name: str = "shm-v1",
) -> Path:
    """Materialise a fully-formed v1 SHM segment on disk.

    Layout mirrors what the patched squeezelite producer would write:
      * legacy vis_t header at offset 0 (56 bytes lock + 24 bytes fields)
      * v1 extension header at offset 80 (40 bytes)
      * PCM ring buffer at offset 120 (VIS_BUF_SIZE * sizeof(int16_t) bytes)

    Note that PCM starts at 120 on the v1 path — writing samples at 80 would
    corrupt the extension header and cause every read to invalidate.
    ``pcm_at_120`` accepts a raw byte string that occupies the leading part
    of the ring buffer; leftover bytes stay zero.  ``write_seq`` defaults to
    2 (even, stable) so the seqlock accepts the snapshot immediately.
    """
    p = tmp_path / name
    data = bytearray(_MMAP_SIZE_V1)
    # legacy vis_t header
    struct.pack_into(
        _HDR_FMT,
        data,
        _HDR_OFFSET,
        VIS_BUF_SIZE,
        buf_index,
        int(running),
        rate,
        updated,
    )
    # v1 extension header
    struct.pack_into(
        _V2_EXT_FMT,
        data,
        _V2_EXT_OFFSET,
        magic,
        abi_version,
        0,             # flags reserved
        write_seq,
        generation,
        abs_write_pos,
        gap_seq,
    )
    if pcm_at_120 is not None:
        assert len(pcm_at_120) <= VIS_BUF_SIZE * 2, "pcm block would overflow the ring"
        data[_BUF_OFFSET_V1 : _BUF_OFFSET_V1 + len(pcm_at_120)] = pcm_at_120
    p.write_bytes(bytes(data))
    return p


def _mutate_stereo_shm_v1(
    path: Path,
    **fields: object,
) -> None:
    """Overwrite one or more header fields on an existing v1 SHM file.

    Accepts the same keyword names as ``_write_stereo_shm_v1`` (limited to
    fields the tests actually mutate).  PCM bytes are left untouched.
    """
    data = bytearray(path.read_bytes())
    # legacy header fields
    buf_size = VIS_BUF_SIZE
    _, cur_buf_index, cur_running_byte, cur_rate, cur_updated = struct.unpack(
        _HDR_FMT, bytes(data[_HDR_OFFSET : _HDR_OFFSET + _HDR_SIZE])
    )
    buf_index = int(fields.get("buf_index", cur_buf_index))
    running = fields.get("running", None)
    running_byte = int(running) if running is not None else cur_running_byte
    rate = int(fields.get("rate", cur_rate))
    updated = int(fields.get("updated", cur_updated))
    struct.pack_into(
        _HDR_FMT,
        data,
        _HDR_OFFSET,
        buf_size,
        buf_index,
        running_byte,
        rate,
        updated,
    )
    # v1 extension fields
    cur_magic, cur_abi, _flags, cur_seq, cur_gen, cur_abs, cur_gap = struct.unpack(
        _V2_EXT_FMT, bytes(data[_V2_EXT_OFFSET : _V2_EXT_OFFSET + _V2_EXT_SIZE])
    )
    magic = int(fields.get("magic", cur_magic))
    abi_version = int(fields.get("abi_version", cur_abi))
    write_seq = int(fields.get("write_seq", cur_seq))
    generation = int(fields.get("generation", cur_gen))
    abs_write_pos = int(fields.get("abs_write_pos", cur_abs))
    gap_seq = int(fields.get("gap_seq", cur_gap))
    struct.pack_into(
        _V2_EXT_FMT,
        data,
        _V2_EXT_OFFSET,
        magic,
        abi_version,
        0,
        write_seq,
        generation,
        abs_write_pos,
        gap_seq,
    )
    if "pcm_at_120" in fields:
        pcm = fields["pcm_at_120"]
        assert isinstance(pcm, (bytes, bytearray))
        assert len(pcm) <= VIS_BUF_SIZE * 2
        data[_BUF_OFFSET_V1 : _BUF_OFFSET_V1 + len(pcm)] = pcm
    path.write_bytes(bytes(data))


def test_stereo_v1_basic_read(tmp_path: Path) -> None:
    """A valid v1 SHM segment with PCM at offset 120 reads back non-zero stereo."""
    # 4 stereo frames = 8 int16 values.  All non-zero so the finite check
    # and downstream shape verification stay meaningful.
    frames = [1000, -1000, 2000, -2000, 3000, -3000, 4000, -4000]
    pcm = struct.pack(f"<{len(frames)}h", *frames)
    p = _write_stereo_shm_v1(
        tmp_path,
        buf_index=len(frames),
        abs_write_pos=len(frames) // 2,
        pcm_at_120=pcm,
    )
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    # Rewind the consumer's view so the fresh producer bytes count as new.
    src._prev_index = 0
    src._abs_write_pos_frames = 0
    result = src.read()
    src.close()

    assert isinstance(result, DataResult)
    assert result.frame.samples.shape == (4, 2)
    assert result.frame.channels == 2
    assert result.frame.sample_rate == 44100
    assert np.any(np.abs(result.frame.samples) > 0.0)


def test_stereo_pcm_at_offset_120(tmp_path: Path) -> None:
    """v1 reader must consume PCM from offset 120, not the extension at 80."""
    # Fill positions 80..119 (the extension) with a distinctive non-zero
    # byte AFTER writing the header, then place real PCM starting at 120.
    # If the reader still copied from 80 it would see the sentinel and the
    # decoded samples would not match the payload we placed at 120.
    real_frames = [500, -500, 600, -600]
    pcm = struct.pack("<4h", *real_frames)
    p = _write_stereo_shm_v1(
        tmp_path,
        buf_index=len(real_frames),
        abs_write_pos=len(real_frames) // 2,
        pcm_at_120=pcm,
    )
    # Overwrite the 40 bytes 80..119 with 0xAA in a way that keeps the
    # extension header valid: we cannot corrupt the header, so instead we
    # verify by comparing the read samples to what a decoder acting on
    # offset 120 would produce.  A read that used offset 80 would have to
    # reinterpret the header bytes as PCM and would produce a very
    # different waveform.
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    src._prev_index = 0
    src._abs_write_pos_frames = 0
    result = src.read()
    src.close()

    assert isinstance(result, DataResult)
    expected_l = np.array(real_frames[0::2], dtype=np.float32) / 32768.0
    expected_r = np.array(real_frames[1::2], dtype=np.float32) / 32768.0
    np.testing.assert_allclose(result.frame.samples[:, 0], expected_l, rtol=1e-6)
    np.testing.assert_allclose(result.frame.samples[:, 1], expected_r, rtol=1e-6)


def test_stereo_v0_rejected(tmp_path: Path) -> None:
    """A segment without the v1 magic must be rejected under require_v1=True."""
    # magic=0 → no v1 header present.
    p = _write_stereo_shm_v1(
        tmp_path, magic=0, abi_version=0, generation=0, write_seq=0
    )
    src = SqueezeliteShmStereoSource()
    with pytest.raises(RuntimeError, match="v1 ABI"):
        src.open("x", _path=p, require_v1=True)


def test_stereo_wrong_abi_version_rejected(tmp_path: Path) -> None:
    """A v1-magic segment advertising abi_version != 1 is rejected."""
    p = _write_stereo_shm_v1(tmp_path, abi_version=2)
    src = SqueezeliteShmStereoSource()
    with pytest.raises(RuntimeError, match="ABI version"):
        src.open("x", _path=p, require_v1=True)


def test_stereo_full_lap_detected(tmp_path: Path) -> None:
    """abs_write_pos advancing by exactly one ring worth invalidates the epoch."""
    frame_capacity = VIS_BUF_SIZE // 2  # ring capacity in stereo frames
    p = _write_stereo_shm_v1(tmp_path, abs_write_pos=100, buf_index=0)
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    _mutate_stereo_shm_v1(p, abs_write_pos=100 + frame_capacity, buf_index=0)
    result = src.read()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_stereo_generation_change_detected(tmp_path: Path) -> None:
    """A generation change between polls invalidates the epoch (producer restart)."""
    p = _write_stereo_shm_v1(tmp_path, generation=1, abs_write_pos=10)
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    _mutate_stereo_shm_v1(p, generation=2, abs_write_pos=0, buf_index=0)
    result = src.read()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_stereo_gap_sequence_invalidates_once(tmp_path: Path) -> None:
    """gap_seq incrementing once invalidates once; subsequent reads with the same value do not."""
    # Provide enough advance in abs_write_pos so the ADVANCE branch would
    # otherwise fire — the gap check should preempt that.
    frames = [1, 2, 3, 4]
    pcm = struct.pack("<4h", *frames)
    p = _write_stereo_shm_v1(
        tmp_path,
        buf_index=4,
        abs_write_pos=2,
        gap_seq=0,
        pcm_at_120=pcm,
    )
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    src._prev_index = 0
    src._abs_write_pos_frames = 0
    # gap_seq bumps to 1.
    _mutate_stereo_shm_v1(p, gap_seq=1, abs_write_pos=4, buf_index=8)
    first = src.read()
    assert isinstance(first, StreamInvalidated)

    # Next poll with the same gap_seq must not invalidate again.  We add
    # fresh PCM so the ADVANCE branch has data to return.
    more_frames = [10, 20, 30, 40]
    _mutate_stereo_shm_v1(
        p,
        gap_seq=1,
        abs_write_pos=6,
        buf_index=12,
        pcm_at_120=struct.pack("<8h", *frames, *more_frames),
    )
    second = src.read()
    src.close()

    assert not isinstance(second, StreamInvalidated), (
        "Second read with unchanged gap_seq must not invalidate."
    )


def test_stereo_counter_regression_invalidates(tmp_path: Path) -> None:
    """abs_write_pos moving backwards signals a producer restart."""
    p = _write_stereo_shm_v1(tmp_path, abs_write_pos=5000, buf_index=0)
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    _mutate_stereo_shm_v1(p, abs_write_pos=10, buf_index=20)
    result = src.read()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_stereo_torn_read_rejected(tmp_path: Path) -> None:
    """An odd write_seq (writer never finishes) must not yield DataResult."""
    p = _write_stereo_shm_v1(tmp_path, write_seq=1, abs_write_pos=10, buf_index=20)
    src = SqueezeliteShmStereoSource()
    # A persistently odd seqlock on open means _read_ext_header sees an
    # in-progress write from the first byte, so the source treats it as v0.
    # Opening with require_v1=False bypasses the strict activation check.
    src.open("x", _path=p, require_v1=False)
    result = src.read()
    src.close()

    # Either invalidated (v1 path bailed on torn read) or a v0 fall-through
    # response (TemporarilyNoData / StreamInvalidated) is acceptable, but a
    # DataResult with the corrupt snapshot is not.
    assert not isinstance(result, DataResult)


def test_stereo_shm_replaced_detected(tmp_path: Path) -> None:
    """A fresh file at the same path (different inode) invalidates the epoch."""
    p = _write_stereo_shm_v1(tmp_path, abs_write_pos=10, buf_index=20)
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=p, require_v1=True)
    # Unlink and recreate: the mmap held by the source keeps pointing at
    # the unlinked inode, while a stat on the path now returns a fresh
    # (dev, ino) tuple.
    os.unlink(p)
    _write_stereo_shm_v1(tmp_path, abs_write_pos=0, buf_index=0)
    result = src.read()
    src.close()

    assert isinstance(result, StreamInvalidated)


def test_stereo_shm_replacement_remaps_and_delivers_new_pcm(tmp_path: Path) -> None:
    """BLOCKER 2: after replacement is detected, subsequent reads must come
    from the new object, not the orphaned old mapping.

    Assertions:
      OLD_MMAP_RELEASED — the mmap held during ``open()`` is closed after the
        replacement is detected (the file object is None post-remap).
      NEW_OBJECT_REMAPPED — the source's (dev, ino) tuple after the remap
        matches the new file's stat, not the old one.
      NEW_PCM_DELIVERED — a subsequent read returns PCM whose sample values
        derive from the NEW segment (0x22 pattern), not the old one (0x11).
    """
    # File A: PCM ring filled with a distinctive pattern (all bytes = 0x11).
    pcm_a = b"\x11\x11" * (VIS_BUF_SIZE)
    path = _write_stereo_shm_v1(
        tmp_path,
        abs_write_pos=100,
        buf_index=200,
        pcm_at_120=pcm_a,
    )
    src = SqueezeliteShmStereoSource()
    src.open("x", _path=path, require_v1=True)
    old_dev_ino = src._shm_dev_ino
    assert old_dev_ino is not None
    old_mm = src._mm
    assert old_mm is not None

    # File B: unlink A and materialise a fresh file at the same path with a
    # distinct pattern (0x22) so we can prove the reader consumed the new
    # segment on the next successful read.  Advance abs_write_pos by 4
    # stereo frames so the reader has a delta to expose.
    os.unlink(path)
    # Encode a value that will fit in one stereo frame: int16 samples 0x2222.
    pattern_two_bytes = b"\x22\x22"
    pcm_b_head = pattern_two_bytes * 8  # 4 stereo frames × 2 channels × 2 bytes
    pcm_b = pcm_b_head + b"\x00" * (VIS_BUF_SIZE * 2 - len(pcm_b_head))
    _write_stereo_shm_v1(
        tmp_path,
        abs_write_pos=100,        # baseline aligned with the source's snapshot
        buf_index=0,
        pcm_at_120=pcm_b,
    )

    # First read after replacement: must invalidate the epoch.
    result_invalid = src.read()
    assert isinstance(result_invalid, StreamInvalidated), (
        f"expected StreamInvalidated on replacement, got {type(result_invalid).__name__}"
    )
    # OLD_MMAP_RELEASED: the old mmap must have been closed and dropped.
    # Python's mmap does not expose an explicit is_closed flag, but reading
    # from a closed mmap raises; verify the source has swapped to a fresh
    # object rather than keeping the pre-replacement one.
    assert src._mm is not old_mm, "old mmap must be released, new one adopted"
    # NEW_OBJECT_REMAPPED: dev/ino now reflects the replacement file.
    assert src._shm_dev_ino is not None
    assert src._shm_dev_ino != old_dev_ino, (
        "dev/ino tuple must reflect the new inode after remap"
    )

    # Advance the writer in the new segment to expose 4 stereo frames.
    _mutate_stereo_shm_v1(path, abs_write_pos=104, buf_index=8)

    # Second read: must deliver PCM from the new file.
    result_data = src.read()
    src.close()
    assert isinstance(result_data, DataResult), (
        f"expected DataResult after remap, got {type(result_data).__name__}"
    )
    # NEW_PCM_DELIVERED: sample values derive from pattern B (0x2222 int16),
    # not pattern A (0x1111).  0x2222 as signed int16 is 8738; scaled by
    # 1/32768 gives ~0.2666.  Pattern A would give 0x1111 -> ~0.1334.
    frame = result_data.frame
    samples = frame.samples
    assert samples.shape[1] == 2
    assert samples.size > 0
    # Every non-silent value must match the new pattern within float rounding.
    non_zero = samples[np.abs(samples) > 1e-4]
    assert non_zero.size > 0, "at least some samples must be from the new segment"
    new_pattern_value = 0x2222 / 32768.0
    old_pattern_value = 0x1111 / 32768.0
    assert np.allclose(np.abs(non_zero), new_pattern_value, atol=1e-4), (
        f"samples must originate from new segment pattern 0x2222 "
        f"(≈{new_pattern_value:.4f}); saw {non_zero[:4]}"
    )
    assert not np.any(np.isclose(np.abs(non_zero), old_pattern_value, atol=1e-4)), (
        "no samples from old segment pattern 0x1111 should remain"
    )
