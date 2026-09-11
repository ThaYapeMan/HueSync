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
import struct as _struct
from pathlib import Path

import numpy as np
import pytest

from huesync.pcm_source import (
    _BUF_OFFSET,
    _HDR_FMT,
    _HDR_OFFSET,
    _HDR_SIZE,
    _MMAP_SIZE,
    AIRPLAY_BYTES_PER_FRAME,
    AIRPLAY_CHANNELS,
    AIRPLAY_SAMPLE_RATE,
    AIRPLAY_SAMPLE_WIDTH,
    VIS_BUF_SIZE,
    WINDOW_SIZE,
    AirPlayPipeSource,
    PcmHpss,
    PcmSource,
    PcmStft,
    SqueezeliteShmSource,
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
) -> bytes:
    """Build a complete vis_t image ready to write to a tempfile."""
    lock_bytes = b"\x00" * _HDR_OFFSET
    header = struct.pack(_HDR_FMT, VIS_BUF_SIZE, buf_index, int(running), rate, 0)
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
# AirPlayPipeSource
# ---------------------------------------------------------------------------


def _stereo_s16le(left: int, right: int, n_frames: int = 1) -> bytes:
    """Build S16_LE stereo bytes: n_frames × (L, R)."""
    frame = _struct.pack("<hh", left, right)
    return frame * n_frames


def test_airplay_contract_constants() -> None:
    """Explicit PCM contract constants must be consistent with each other."""
    assert AIRPLAY_CHANNELS == 2
    assert AIRPLAY_SAMPLE_WIDTH == 2  # S16_LE
    assert AIRPLAY_BYTES_PER_FRAME == AIRPLAY_CHANNELS * AIRPLAY_SAMPLE_WIDTH


def test_airplay_source_satisfies_protocol() -> None:
    """AirPlayPipeSource must satisfy the PcmSource Protocol at runtime."""
    assert isinstance(AirPlayPipeSource(), PcmSource)


def test_airplay_sample_rate() -> None:
    src = AirPlayPipeSource()
    assert src.sample_rate == AIRPLAY_SAMPLE_RATE


def test_airplay_running_false_before_data() -> None:
    """running must be False until read_new() has returned non-empty samples."""
    src = AirPlayPipeSource()
    assert src.running is False


def test_airplay_read_new_without_open_returns_empty() -> None:
    src = AirPlayPipeSource()
    result = src.read_new()
    assert len(result) == 0
    assert result.dtype == np.float32


def test_airplay_read_new_stereo_to_mono() -> None:
    """S16_LE stereo → average L+R → float32 in [−1, 1]."""
    r_fd, w_fd = os.pipe()
    try:
        # Write one stereo frame: L=16384 (0.5), R=−16384 (−0.5) → average 0.0
        os.write(w_fd, _stereo_s16le(16384, -16384))
        src = AirPlayPipeSource()
        src._fd = r_fd  # inject the read end directly
        src._last_data_t = None
        result = src.read_new()
        src._fd = None  # prevent double-close
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass

    assert len(result) == 1
    assert result.dtype == np.float32
    assert abs(result[0]) < 1e-5  # (0.5 + −0.5) / 2 = 0.0


def test_airplay_read_new_unity_amplitude() -> None:
    """Maximum positive s16 value must map to ≈ 1.0 when both channels equal."""
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, _stereo_s16le(32767, 32767))
        src = AirPlayPipeSource()
        src._fd = r_fd
        src._last_data_t = None
        result = src.read_new()
        src._fd = None
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass

    assert len(result) == 1
    assert abs(result[0] - 32767 / 32768.0) < 1e-4


def test_airplay_running_true_after_data() -> None:
    """running must be True immediately after read_new() returns data."""
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, _stereo_s16le(1000, 1000, n_frames=4))
        src = AirPlayPipeSource()
        src._fd = r_fd
        src._last_data_t = None
        src.read_new()
        src._fd = None
        assert src.running is True
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass


def test_airplay_incomplete_frame_carried() -> None:
    """Trailing bytes that do not complete a stereo frame are carried to the next read."""
    r_fd, w_fd = os.pipe()
    try:
        # Write 1 complete frame (4 bytes) + 1 orphan byte.
        os.write(w_fd, _stereo_s16le(100, 200) + b"\xff")
        src = AirPlayPipeSource()
        src._fd = r_fd
        src._last_data_t = None
        result = src.read_new()
        remainder = src._remainder
        src._fd = None
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass

    assert len(result) == 1  # only the complete frame decoded
    assert remainder == b"\xff"  # orphan byte is held for the next read


def test_airplay_partial_frame_joined_across_reads() -> None:
    """A frame split across two reads is decoded correctly on the second read."""
    r_fd, w_fd = os.pipe()
    try:
        # First read delivers 3 bytes (incomplete frame of 4).
        os.write(w_fd, b"\x00\x40\x00")  # first 3 of 4 bytes for L=0x4000, R=...
        src = AirPlayPipeSource()
        src._fd = r_fd
        src._last_data_t = None
        result1 = src.read_new()

        # Second read delivers the missing byte plus another complete frame.
        frame2 = _stereo_s16le(1000, -1000)
        os.write(w_fd, b"\x80" + frame2)  # completes first frame + adds second
        result2 = src.read_new()
        src._fd = None
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass

    assert len(result1) == 0  # incomplete frame — nothing yet
    assert src._remainder == b""  # all bytes now consumed
    assert len(result2) == 2  # first (reconstructed) + second frame


def test_airplay_no_channel_swap_after_partial_read() -> None:
    """L/R channels must not swap after a read that left sub-frame remainder bytes."""
    r_fd, w_fd = os.pipe()
    try:
        # Frame: L=+32767 (~1.0), R=0 → mono ≈ 0.5
        frame = _stereo_s16le(32767, 0)
        # Split: write first 3 bytes, then the last byte.
        os.write(w_fd, frame[:3])
        src = AirPlayPipeSource()
        src._fd = r_fd
        src._last_data_t = None
        src.read_new()  # partial — carries 3 bytes

        os.write(w_fd, frame[3:4])  # final byte of first frame
        result = src.read_new()
        src._fd = None
    finally:
        os.close(w_fd)
        try:
            os.close(r_fd)
        except OSError:
            pass

    assert len(result) == 1
    # L=32767, R=0 → (32767 + 0) / (2 × 32768) ≈ 0.5
    assert abs(result[0] - 32767 / (2.0 * 32768.0)) < 1e-4
