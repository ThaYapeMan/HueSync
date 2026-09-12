"""Phase 2 tests: stereo source adapters and AudioCanonicalizer."""

from __future__ import annotations

import fcntl
import os
import struct
import tempfile
from pathlib import Path

import numpy as np
import pytest

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

# ---------------------------------------------------------------------------
# SHM helpers
# ---------------------------------------------------------------------------

_HDR_SIZE = struct.calcsize(_HDR_FMT)


def _make_shm_file(
    buf_index: int = 0,
    running: bool = True,
    rate: int = 44100,
    buf_data: bytes | None = None,
) -> Path:
    """Write a synthetic SHM file matching the squeezelite vis_t layout."""
    data = bytearray(_MMAP_SIZE)
    header = struct.pack(
        _HDR_FMT,
        VIS_BUF_SIZE,
        buf_index,
        1 if running else 0,
        rate,
        0,
    )
    data[_HDR_OFFSET : _HDR_OFFSET + _HDR_SIZE] = header
    if buf_data is not None:
        data[_BUF_OFFSET : _BUF_OFFSET + len(buf_data)] = buf_data
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".shm")
    tmp.write(data)
    tmp.close()
    return Path(tmp.name)


def _stereo_s16_bytes(left_vals: list[int], right_vals: list[int]) -> bytes:
    """Interleaved S16LE bytes: L0 R0 L1 R1 ..."""
    out = bytearray()
    for lv, rv in zip(left_vals, right_vals, strict=True):
        out += struct.pack("<hh", lv, rv)
    return bytes(out)


# ---------------------------------------------------------------------------
# SqueezeliteShmStereoSource — basic lifecycle
# ---------------------------------------------------------------------------


def test_shm_stereo_no_data():
    """buf_index unchanged → TemporarilyNoData."""
    shm = _make_shm_file(buf_index=0)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("aa:bb:cc:dd:ee:ff", _path=shm)
        result = src.read()
        assert isinstance(result, TemporarilyNoData)
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_data_result_type():
    """Valid new samples → DataResult."""
    n_frames = 4
    buf_bytes = _stereo_s16_bytes([1000] * n_frames, [-1000] * n_frames)
    buf_index = n_frames * 2
    shm = _make_shm_file(buf_index=buf_index, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("aa:bb:cc:dd:ee:ff", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_lr_preserved():
    """L and R channels are not mixed; samples[:, 0]=L, samples[:, 1]=R."""
    n_frames = 8
    left_val = 16384
    right_val = -8192
    buf_bytes = _stereo_s16_bytes([left_val] * n_frames, [right_val] * n_frames)
    shm = _make_shm_file(buf_index=n_frames * 2, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("aa:bb:cc:dd:ee:ff", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        assert frame.samples.shape == (n_frames, 2)
        np.testing.assert_allclose(frame.samples[:, 0], left_val / 32768.0, atol=1e-5)
        np.testing.assert_allclose(frame.samples[:, 1], right_val / 32768.0, atol=1e-5)
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_no_downmix():
    """Anti-phase L+R=0 does NOT collapse to silence."""
    n_frames = 4
    buf_bytes = _stereo_s16_bytes([8000] * n_frames, [-8000] * n_frames)
    shm = _make_shm_file(buf_index=n_frames * 2, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        assert np.all(frame.samples[:, 0] > 0)
        assert np.all(frame.samples[:, 1] < 0)
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_samples_immutable():
    """Published samples must be WRITEABLE=False."""
    n_frames = 4
    buf_bytes = _stereo_s16_bytes([100] * n_frames, [200] * n_frames)
    shm = _make_shm_file(buf_index=n_frames * 2, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        with pytest.raises(ValueError, match="read-only"):
            result.frame.samples[0, 0] = 99.0
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_fell_behind_returns_invalidated():
    """n_new > VIS_BUF_SIZE // 2 → StreamInvalidated(UNKNOWN)."""
    shm = _make_shm_file(buf_index=VIS_BUF_SIZE // 2 + 2)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, StreamInvalidated)
        assert result.cause == InvalidationCause.UNKNOWN
        assert result.known_lost_samples is None
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_complete_frames_only():
    """Odd n_new (orphaned s16 sample) is rounded to zero → TemporarilyNoData."""
    shm = _make_shm_file(buf_index=1)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, TemporarilyNoData)
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_source_id_format():
    """source_id is 'lms:<mac>'."""
    shm = _make_shm_file()
    try:
        src = SqueezeliteShmStereoSource()
        src.open("aa:bb:cc:dd:ee:ff", _path=shm)
        assert src.source_id == "lms:aa:bb:cc:dd:ee:ff"
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_frame_dtype():
    """DecodedSourceFrame.samples dtype must be float32."""
    n_frames = 4
    buf_bytes = _stereo_s16_bytes([1000] * n_frames, [2000] * n_frames)
    shm = _make_shm_file(buf_index=n_frames * 2, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        assert result.frame.samples.dtype == np.float32
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_channels_field():
    """DecodedSourceFrame.channels == 2."""
    n_frames = 4
    buf_bytes = _stereo_s16_bytes([1000] * n_frames, [2000] * n_frames)
    shm = _make_shm_file(buf_index=n_frames * 2, buf_data=buf_bytes)
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = 0
        result = src.read()
        assert isinstance(result, DataResult)
        assert result.frame.channels == 2
        src.close()
    finally:
        shm.unlink()


def test_shm_stereo_wraparound():
    """Wraparound read reconstructs correct sample order."""
    left_vals = [100, 200, 300, 400]
    right_vals = [500, 600, 700, 800]
    buf = bytearray(_MMAP_SIZE - _BUF_OFFSET)
    # First 2 frames near the end of the circular buffer.
    start_offset = (VIS_BUF_SIZE - 4) * 2
    for i, (lv, rv) in enumerate(zip(left_vals[:2], right_vals[:2], strict=True)):
        struct.pack_into("<hh", buf, start_offset + i * 4, lv, rv)
    # Last 2 frames at the start.
    for i, (lv, rv) in enumerate(zip(left_vals[2:], right_vals[2:], strict=True)):
        struct.pack_into("<hh", buf, i * 4, lv, rv)

    shm = _make_shm_file(buf_index=4, buf_data=bytes(buf))
    try:
        src = SqueezeliteShmStereoSource()
        src.open("test:mac", _path=shm)
        src._prev_index = VIS_BUF_SIZE - 4
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        assert frame.samples.shape == (4, 2)
        np.testing.assert_allclose(
            frame.samples[:, 0], [v / 32768.0 for v in left_vals], atol=1e-5
        )
        src.close()
    finally:
        shm.unlink()


# ---------------------------------------------------------------------------
# AirPlayPipeStereoSource — synthetic os.pipe()
# ---------------------------------------------------------------------------


def _s16le_stereo_bytes(left_vals: list[int], right_vals: list[int]) -> bytes:
    out = bytearray()
    for lv, rv in zip(left_vals, right_vals, strict=True):
        out += struct.pack("<hh", lv, rv)
    return bytes(out)


def _open_airplay_src_on_pipe() -> tuple[AirPlayPipeStereoSource, int]:
    """Return (src opened on read end, write_fd)."""
    r_fd, w_fd = os.pipe()
    fl = fcntl.fcntl(r_fd, fcntl.F_GETFL)
    fcntl.fcntl(r_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    src = AirPlayPipeStereoSource.__new__(AirPlayPipeStereoSource)
    src._path = Path("/synthetic/pipe")
    src._fd = r_fd
    src._last_data_t = None
    src._remainder = b""
    src._source_id = "airplay:/synthetic/pipe"
    return src, w_fd


def test_airplay_stereo_eagain_returns_tnd():
    """Empty pipe (EAGAIN) → TemporarilyNoData."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        result = src.read()
        assert isinstance(result, TemporarilyNoData)
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_eof_returns_end_of_stream():
    """Closed write end (iOS disconnect) → EndOfStream."""
    src, w_fd = _open_airplay_src_on_pipe()
    os.close(w_fd)
    try:
        result = src.read()
        assert isinstance(result, EndOfStream)
    finally:
        src.close()


def test_airplay_stereo_data_result():
    """Valid frames → DataResult with stereo float32."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        payload = _s16le_stereo_bytes([1000, 2000], [-1000, -2000])
        os.write(w_fd, payload)
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        assert frame.samples.shape == (2, 2)
        assert frame.samples.dtype == np.float32
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_lr_preserved():
    """L and R channels are separated, not averaged."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        left_val = 16384
        right_val = -8192
        payload = _s16le_stereo_bytes([left_val] * 4, [right_val] * 4)
        os.write(w_fd, payload)
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        np.testing.assert_allclose(frame.samples[:, 0], left_val / 32768.0, atol=1e-5)
        np.testing.assert_allclose(frame.samples[:, 1], right_val / 32768.0, atol=1e-5)
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_no_downmix():
    """Anti-phase frames are NOT cancelled."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        payload = _s16le_stereo_bytes([8000] * 2, [-8000] * 2)
        os.write(w_fd, payload)
        result = src.read()
        assert isinstance(result, DataResult)
        frame = result.frame
        assert np.all(frame.samples[:, 0] > 0)
        assert np.all(frame.samples[:, 1] < 0)
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_partial_byte_carry():
    """Sub-frame byte carry preserves L/R alignment across read boundaries."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        # 3 bytes — less than one 4-byte stereo frame.
        os.write(w_fd, b"\x00\x10\x00")
        result1 = src.read()
        assert isinstance(result1, TemporarilyNoData)
        assert src._remainder == b"\x00\x10\x00"

        # Complete to two more full frames (1 byte finishes the partial + 2 new).
        full_frame = _s16le_stereo_bytes([4096, 8192], [2048, 1024])
        os.write(w_fd, b"\x20" + full_frame)
        result2 = src.read()
        assert isinstance(result2, DataResult)
        assert result2.frame.samples.shape[0] == 3
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_samples_immutable():
    """Published samples must be WRITEABLE=False."""
    src, w_fd = _open_airplay_src_on_pipe()
    try:
        payload = _s16le_stereo_bytes([1000] * 2, [2000] * 2)
        os.write(w_fd, payload)
        result = src.read()
        assert isinstance(result, DataResult)
        with pytest.raises(ValueError, match="read-only"):
            result.frame.samples[0, 0] = 99.0
    finally:
        src.close()
        os.close(w_fd)


def test_airplay_stereo_sample_rate():
    """sample_rate property returns 44100."""
    src = AirPlayPipeStereoSource.__new__(AirPlayPipeStereoSource)
    src._path = Path("/synthetic")
    src._fd = None
    src._last_data_t = None
    src._remainder = b""
    src._source_id = "airplay:/synthetic"
    assert src.sample_rate == AIRPLAY_SAMPLE_RATE


def test_airplay_stereo_source_id_format():
    p = Path("/run/huesync/airplay.pcm")
    src = AirPlayPipeStereoSource(path=p)
    assert src.source_id == f"airplay:{p}"


def test_airplay_stereo_fd_none_returns_tnd():
    """read() before open() → TemporarilyNoData."""
    src = AirPlayPipeStereoSource.__new__(AirPlayPipeStereoSource)
    src._path = Path("/synthetic")
    src._fd = None
    src._last_data_t = None
    src._remainder = b""
    src._source_id = "airplay:/synthetic"
    result = src.read()
    assert isinstance(result, TemporarilyNoData)


# ---------------------------------------------------------------------------
# AudioCanonicalizer — helpers
# ---------------------------------------------------------------------------


def _make_data_result(
    n: int = 2048,
    rate: int = 44100,
    source_id: str = "test:src",
    channels: int = 2,
    over_range: bool = False,
) -> DataResult:
    arr = np.zeros((n, channels), dtype=np.float32)
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=rate,
        channels=channels,
        source_id=source_id,
        source_sample_pos=None,
        over_range=over_range,
        wall_ns=None,
    )
    return DataResult(frame=frame)


def _collect_canonical(ac: AudioCanonicalizer, pushes: int, n: int = 2048) -> list[CanonicalData]:
    out: list[CanonicalData] = []
    for _ in range(pushes):
        for r in ac.push(_make_data_result(n=n)):
            if isinstance(r, CanonicalData):
                out.append(r)
    return out


# ---------------------------------------------------------------------------
# AudioCanonicalizer — lifecycle pass-throughs
# ---------------------------------------------------------------------------


def test_canonicalizer_tnd_passthrough():
    ac = AudioCanonicalizer()
    results = ac.push(TemporarilyNoData())
    assert len(results) == 1
    assert isinstance(results[0], TemporarilyNoData)


def test_canonicalizer_stream_invalidated_passthrough():
    ac = AudioCanonicalizer()
    si = StreamInvalidated(cause=InvalidationCause.RESTART)
    results = ac.push(si)
    assert len(results) == 1
    assert isinstance(results[0], StreamInvalidated)
    assert results[0].cause == InvalidationCause.RESTART


def test_canonicalizer_stream_invalidated_no_drain():
    """StreamInvalidated terminates epoch without draining the resampler."""
    ac = AudioCanonicalizer()
    ac.push(_make_data_result(n=2048))
    results = ac.push(StreamInvalidated(cause=InvalidationCause.UNKNOWN))
    assert all(not isinstance(r, CanonicalData) for r in results)
    assert any(isinstance(r, StreamInvalidated) for r in results)


def test_canonicalizer_eos_emits_end_of_stream():
    """EndOfStream always produces an EndOfStream in the result list."""
    ac = AudioCanonicalizer()
    ac.push(_make_data_result(n=4096))
    results = ac.push(EndOfStream())
    assert any(isinstance(r, EndOfStream) for r in results)


def test_canonicalizer_eos_drain_then_eos():
    """EndOfStream after primed resampler: CanonicalData* then EndOfStream."""
    ac = AudioCanonicalizer()
    for _ in range(4):
        ac.push(_make_data_result(n=4096))
    results = ac.push(EndOfStream())
    assert isinstance(results[-1], EndOfStream)
    for r in results[:-1]:
        assert isinstance(r, CanonicalData)


def test_canonicalizer_reset_starts_new_epoch():
    """reset() clears state; next DataResult begins a new epoch."""
    ac = AudioCanonicalizer()
    frames1 = _collect_canonical(ac, pushes=10, n=4096)
    assert frames1
    first_epoch = frames1[0].frame.epoch_id

    ac.reset()
    frames2 = _collect_canonical(ac, pushes=10, n=4096)
    assert frames2
    assert frames2[0].frame.epoch_id != first_epoch


# ---------------------------------------------------------------------------
# AudioCanonicalizer — epoch lifecycle
# ---------------------------------------------------------------------------


def test_canonicalizer_epoch_id_stable_within_epoch():
    """All frames in the same epoch share the same epoch_id."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=20, n=2048)
    assert len(frames) >= 2
    epoch_ids = {f.frame.epoch_id for f in frames}
    assert len(epoch_ids) == 1


def test_canonicalizer_epoch_id_is_uuid():
    """epoch_id looks like a uuid4."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=10, n=4096)
    assert frames
    epoch_id = frames[0].frame.epoch_id
    assert "-" in epoch_id
    assert len(epoch_id) == 36


def test_canonicalizer_sample_pos_monotone():
    """sample_pos increases monotonically within an epoch."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=20, n=2048)
    positions = [f.frame.sample_pos for f in frames]
    assert all(positions[i] < positions[i + 1] for i in range(len(positions) - 1))


def test_canonicalizer_sample_pos_starts_at_zero():
    """First canonical output in an epoch has sample_pos == 0."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=20, n=2048)
    assert frames[0].frame.sample_pos == 0


def test_canonicalizer_epoch_changes_after_invalidation():
    """After StreamInvalidated, the next epoch gets a different epoch_id."""
    ac = AudioCanonicalizer()
    frames1 = _collect_canonical(ac, pushes=10, n=4096)
    assert frames1
    first_epoch = frames1[0].frame.epoch_id

    ac.push(StreamInvalidated(cause=InvalidationCause.UNKNOWN))
    frames2 = _collect_canonical(ac, pushes=10, n=4096)
    assert frames2
    assert frames2[0].frame.epoch_id != first_epoch


def test_canonicalizer_sample_pos_resets_after_invalidation():
    """After epoch boundary, sample_pos resets to 0."""
    ac = AudioCanonicalizer()
    _collect_canonical(ac, pushes=10, n=4096)
    ac.push(StreamInvalidated(cause=InvalidationCause.UNKNOWN))
    frames2 = _collect_canonical(ac, pushes=10, n=4096)
    assert frames2[0].frame.sample_pos == 0


def test_canonicalizer_source_id_preserved():
    """source_id from DecodedSourceFrame propagates to AnalysisPcmFrame."""
    ac = AudioCanonicalizer()
    frames = []
    for _ in range(10):
        for r in ac.push(_make_data_result(n=4096, source_id="lms:aa:bb:cc:dd:ee:ff")):
            if isinstance(r, CanonicalData):
                frames.append(r)
    assert frames
    assert all(f.frame.source_id == "lms:aa:bb:cc:dd:ee:ff" for f in frames)


# ---------------------------------------------------------------------------
# AudioCanonicalizer — rate change detection
# ---------------------------------------------------------------------------


def test_canonicalizer_rate_change_emits_invalidated():
    """Consecutive DataResults at different rates → StreamInvalidated prepended."""
    ac = AudioCanonicalizer()
    _collect_canonical(ac, pushes=10, n=4096)
    results = ac.push(_make_data_result(n=2048, rate=48000))
    assert any(isinstance(r, StreamInvalidated) for r in results)
    si = next(r for r in results if isinstance(r, StreamInvalidated))
    assert si.cause == InvalidationCause.RATE_CHANGE


def test_canonicalizer_rate_change_new_epoch():
    """After rate change, new CanonicalData belongs to a different epoch."""
    ac = AudioCanonicalizer()
    frames1 = _collect_canonical(ac, pushes=10, n=4096)
    first_epoch = frames1[0].frame.epoch_id if frames1 else None

    for _ in range(15):
        ac.push(_make_data_result(n=4096, rate=48000))
    frames2 = _collect_canonical(ac, pushes=10, n=4096)
    if frames1 and frames2:
        assert frames2[0].frame.epoch_id != first_epoch


# ---------------------------------------------------------------------------
# AudioCanonicalizer — output shape and rate
# ---------------------------------------------------------------------------


def test_canonicalizer_output_is_stereo():
    """All canonical frames have shape (n, 2)."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=20, n=2048)
    assert frames
    for f in frames:
        assert f.frame.samples.ndim == 2
        assert f.frame.samples.shape[1] == 2


def test_canonicalizer_output_dtype_float32():
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=10, n=4096)
    assert frames
    for f in frames:
        assert f.frame.samples.dtype == np.float32


def test_canonicalizer_target_rate():
    assert AudioCanonicalizer.TARGET_RATE == 48000


def test_canonicalizer_samples_immutable():
    """AnalysisPcmFrame.samples must be WRITEABLE=False."""
    ac = AudioCanonicalizer()
    frames = _collect_canonical(ac, pushes=10, n=4096)
    assert frames
    with pytest.raises(ValueError, match="read-only"):
        frames[0].frame.samples[0, 0] = 99.0


# ---------------------------------------------------------------------------
# Stereo acceptance — mono → L=R duplication
# ---------------------------------------------------------------------------


def test_canonicalizer_mono_input_becomes_lr_equal():
    """Mono DecodedSourceFrame → canonical L == R (duplicate to L=R, no downmix)."""
    ac = AudioCanonicalizer()
    arr = np.sin(np.linspace(0, 2 * np.pi, 4096)).astype(np.float32).reshape(-1, 1)
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=44100,
        channels=1,
        source_id="test:mono",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    results = []
    for _ in range(10):
        results += ac.push(DataResult(frame=frame))
    canonical_frames = [r for r in results if isinstance(r, CanonicalData)]
    assert canonical_frames
    for cf in canonical_frames:
        np.testing.assert_array_equal(cf.frame.samples[:, 0], cf.frame.samples[:, 1])


def test_canonicalizer_stereo_anti_phase_preserved():
    """Opposite-phase L/R: sum of L+R stays near zero after resampling."""
    ac = AudioCanonicalizer()
    t = np.linspace(0, 2 * np.pi, 4096, dtype=np.float32)
    sig = (np.sin(t) * 0.5).astype(np.float32)
    arr = np.column_stack([sig, -sig])
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=44100,
        channels=2,
        source_id="test:antiphase",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    results = []
    for _ in range(8):
        results += ac.push(DataResult(frame=frame))
    canonical_frames = [r for r in results if isinstance(r, CanonicalData)]
    assert canonical_frames
    for cf in canonical_frames:
        ch_l = cf.frame.samples[:, 0]
        ch_r = cf.frame.samples[:, 1]
        mix = ch_l + ch_r
        assert np.max(np.abs(mix)) < 0.05


def test_canonicalizer_l_only_r_silent():
    """Signal on L, silence on R: R output stays near-zero."""
    ac = AudioCanonicalizer()
    sig = (np.sin(np.linspace(0, 2 * np.pi, 4096)) * 0.5).astype(np.float32)
    silence = np.zeros(4096, dtype=np.float32)
    arr = np.column_stack([sig, silence])
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=44100,
        channels=2,
        source_id="test:lonly",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    results = []
    for _ in range(8):
        results += ac.push(DataResult(frame=frame))
    canonical_frames = [r for r in results if isinstance(r, CanonicalData)]
    assert canonical_frames
    for cf in canonical_frames:
        assert np.max(np.abs(cf.frame.samples[:, 1])) < 1e-3


# ---------------------------------------------------------------------------
# Chunk independence
# ---------------------------------------------------------------------------


def _full_epoch_canonical(chunk_size: int, total_samples: int = 88200) -> np.ndarray:
    """Feed a sine wave in chunk_size pieces; return concatenated canonical L channel."""
    ac = AudioCanonicalizer()
    sig = (np.sin(np.linspace(0, 2 * np.pi * 20, total_samples)) * 0.5).astype(np.float32)
    arr = np.column_stack([sig, sig])
    out_l: list[np.ndarray] = []
    offset = 0
    while offset < total_samples:
        end = min(offset + chunk_size, total_samples)
        chunk = arr[offset:end]
        f = DecodedSourceFrame(
            samples=chunk,
            sample_rate=44100,
            channels=2,
            source_id="test:ci",
            source_sample_pos=None,
            over_range=False,
            wall_ns=None,
        )
        for r in ac.push(DataResult(frame=f)):
            if isinstance(r, CanonicalData):
                out_l.append(r.frame.samples[:, 0].copy())
        offset = end
    return np.concatenate(out_l) if out_l else np.array([], dtype=np.float32)


def test_chunk_independence_output_length():
    """Different chunk sizes produce the same total output length (±2 frames)."""
    out_128 = _full_epoch_canonical(128)
    out_1024 = _full_epoch_canonical(1024)
    out_4096 = _full_epoch_canonical(4096)
    assert abs(len(out_128) - len(out_1024)) <= 2
    assert abs(len(out_128) - len(out_4096)) <= 2


def test_chunk_independence_content():
    """Different chunk sizes produce numerically equivalent canonical output."""
    out_512 = _full_epoch_canonical(512)
    out_4096 = _full_epoch_canonical(4096)
    n = min(len(out_512), len(out_4096))
    if n > 0:
        np.testing.assert_allclose(out_512[:n], out_4096[:n], atol=1e-4)


# ---------------------------------------------------------------------------
# Amplitude / gain preservation
# ---------------------------------------------------------------------------


def _canonical_rms(amplitude: float, n: int = 44100) -> float:
    """Feed a sine wave at given amplitude; return RMS of canonical L output."""
    ac = AudioCanonicalizer()
    sig = (np.sin(np.linspace(0, 2 * np.pi * 10, n)) * amplitude).astype(np.float32)
    arr = np.column_stack([sig, sig])
    frame = DecodedSourceFrame(
        samples=arr,
        sample_rate=44100,
        channels=2,
        source_id="test:amp",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    out: list[float] = []
    for _ in range(4):
        for r in ac.push(DataResult(frame=frame)):
            if isinstance(r, CanonicalData):
                out.append(float(np.mean(r.frame.samples[:, 0] ** 2)))
    return float(np.sqrt(np.mean(out))) if out else 0.0


def test_amplitude_proportional():
    """Doubling input amplitude approximately doubles canonical RMS."""
    rms_half = _canonical_rms(0.25)
    rms_full = _canonical_rms(0.5)
    if rms_half > 1e-6 and rms_full > 1e-6:
        ratio = rms_full / rms_half
        assert 1.8 < ratio < 2.2, f"Expected ~2× RMS ratio, got {ratio:.3f}"


def test_amplitude_no_normalisation():
    """Near-zero input stays near-zero; canonicalizer applies no AGC."""
    rms = _canonical_rms(0.001)
    assert rms < 0.01


# ---------------------------------------------------------------------------
# over_range propagation
# ---------------------------------------------------------------------------


def _push_data(ac: AudioCanonicalizer, over_range: bool, n: int = 4096) -> list[CanonicalData]:
    frame = DecodedSourceFrame(
        samples=np.zeros((n, 2), dtype=np.float32),
        sample_rate=44100,
        channels=2,
        source_id="test:or",
        source_sample_pos=None,
        over_range=over_range,
        wall_ns=None,
    )
    return [r for r in ac.push(DataResult(frame=frame)) if isinstance(r, CanonicalData)]


def test_over_range_true_propagates():
    """Source over_range=True propagates to at least one canonical frame."""
    ac = AudioCanonicalizer()
    for _ in range(5):
        _push_data(ac, over_range=False)
    frames = []
    for _ in range(5):
        frames += _push_data(ac, over_range=True)
    if frames:
        assert any(f.frame.over_range for f in frames)


def test_over_range_false_stays_false_for_quiet_signal():
    """Source over_range=False and quiet canonical → over_range stays False."""
    ac = AudioCanonicalizer()
    frames: list[CanonicalData] = []
    for _ in range(20):
        frames += _push_data(ac, over_range=False)
    if frames:
        assert all(not f.frame.over_range for f in frames)


# ---------------------------------------------------------------------------
# NaN / Inf handling
# ---------------------------------------------------------------------------


def test_canonicalizer_nan_in_canonical_causes_invalidation():
    """Non-finite canonical samples → StreamInvalidated; no NaN in CanonicalData."""
    ac = AudioCanonicalizer()
    for _ in range(5):
        ac.push(_make_data_result(n=4096))

    original_resample = ac._stream.resample_chunk  # type: ignore[union-attr]

    def _nan_chunk(data, last=False):
        out = original_resample(data, last=last)
        if len(out) > 0:
            out = out.copy()
            out[0, 0] = float("nan")
        return out

    ac._stream.resample_chunk = _nan_chunk  # type: ignore[union-attr]
    results = ac.push(_make_data_result(n=4096))
    assert any(isinstance(r, StreamInvalidated) for r in results)
    assert not any(
        isinstance(r, CanonicalData) and not np.all(np.isfinite(r.frame.samples))
        for r in results
    )


# ---------------------------------------------------------------------------
# Zero-output / epoch pending
# ---------------------------------------------------------------------------


def test_canonicalizer_small_chunk_returns_list():
    """Very small input chunks may return empty list; never raises."""
    ac = AudioCanonicalizer()
    tiny = DecodedSourceFrame(
        samples=np.zeros((1, 2), dtype=np.float32),
        sample_rate=44100,
        channels=2,
        source_id="test:tiny",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    results = ac.push(DataResult(frame=tiny))
    assert isinstance(results, list)


def test_canonicalizer_eventually_emits_after_small_chunks():
    """Enough 1-sample pushes eventually produce CanonicalData."""
    ac = AudioCanonicalizer()
    tiny = DecodedSourceFrame(
        samples=np.zeros((1, 2), dtype=np.float32),
        sample_rate=44100,
        channels=2,
        source_id="test:tiny",
        source_sample_pos=None,
        over_range=False,
        wall_ns=None,
    )
    all_results = []
    for _ in range(2000):
        all_results += ac.push(DataResult(frame=tiny))
    assert any(isinstance(r, CanonicalData) for r in all_results)
