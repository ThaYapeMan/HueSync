"""Python binding for upstream cavacore via ctypes.

Upstream: github.com/karlstav/cava  commit 6d43df3b2c7882122585c02c064b20009842a6f8
License:  MIT — Copyright (c) 2022 Karl Stavestrand <karl@stavestrand.no>

The native shared library (_libcavacore.so) is compiled from vendored C sources
by hatch_build.py during `pip install .`.  Import of this module does NOT raise;
call is_cavacore_available() to check whether the native library is present and
loads successfully before constructing CavaCoreBackend.

Amplitude convention
--------------------
Upstream cavacore's LINEAR scaling mode (SCALING_LINEAR=0) is calibrated for
PCM in the int16 range, approximately [-32768, 32768].  Canonical HueSync PCM
is normalized float32 in approximately [-1.0, 1.0].  CavaCoreBackend.execute()
scales incoming samples by 32768 at the cavacore boundary; canonical PCM is
never modified.  SCALING_DECIBEL mode already divides by 32768 internally
(see cavacore.c) and is not used by HueSync.

Execution scheduler
-------------------
execute() processes samples in deterministic 480-frame execution blocks via a
carry buffer.  Samples that do not fill a complete block are buffered and
combined with the next call's samples.  This makes output sample-count-driven
rather than call-count-driven: identical total PCM always produces identical
cavacore state regardless of incoming chunk sizes.

Stereo channel convention (matches cavacore.c buffer extraction)
----------------------------------------------------------------
cava_in interleaved as [L0, R0, L1, R1, …]
cava_out[:n_bars] = L bars, cava_out[n_bars:] = R bars

For the HueSync integration the caller averages L+R bars after execute().

Lifecycle
---------
CavaCoreBackend.close() / context-manager usage releases the cava_plan and
FFTW memory.  close() is idempotent.  __del__ is safe even after a failed
__init__.  If cava_init() reports an error status the plan struct is freed via
cavacore_free_failed() (not cava_destroy()) to avoid freeing uninitialised
inner pointers.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np

_SO_PATH = Path(__file__).parent / "_libcavacore.so"

# Execution block size in frames.  Matches the HueSync canonical hop (480 frames
# at 48 kHz = 10 ms).  cava_execute() is called with exactly this many frames
# per channel, regardless of incoming chunk sizes.
_EXEC_BLOCK_FRAMES = 480

# Amplitude scale applied at the cavacore boundary.
# cavacore EQ is calibrated for int16 PCM range (~[-32768, 32768]).
# Canonical HueSync PCM is float32 in ~[-1.0, 1.0].
_PCM_SCALE = 32768.0


def _load_lib() -> ctypes.CDLL:
    if not _SO_PATH.exists():
        raise RuntimeError(
            f"cavacore shared library not found at {_SO_PATH}.\n"
            "Build it with: pip install .  (requires libfftw3-dev on the host)"
        )
    lib = ctypes.CDLL(str(_SO_PATH))

    c_int = ctypes.c_int
    c_uint = ctypes.c_uint
    c_double = ctypes.c_double
    c_void_p = ctypes.c_void_p
    c_char_p = ctypes.c_char_p
    POINTER = ctypes.POINTER

    lib.cava_init.restype = c_void_p
    lib.cava_init.argtypes = [c_int, c_uint, c_int, c_int, c_double, c_int, c_int, c_int]

    lib.cava_execute.restype = None
    lib.cava_execute.argtypes = [POINTER(c_double), c_int, POINTER(c_double), c_void_p]

    lib.cava_destroy.restype = None
    lib.cava_destroy.argtypes = [c_void_p]

    # Bridge helpers — expose cava_plan fields without a full ctypes struct definition
    lib.cavacore_status.restype = c_int
    lib.cavacore_status.argtypes = [c_void_p]

    lib.cavacore_n_bars.restype = c_int
    lib.cavacore_n_bars.argtypes = [c_void_p]

    lib.cavacore_channels.restype = c_int
    lib.cavacore_channels.argtypes = [c_void_p]

    lib.cavacore_error.restype = c_char_p
    lib.cavacore_error.argtypes = [c_void_p]

    # Free a plan returned from cava_init() with status != 0 (partial init only).
    # Do NOT call cava_destroy() on a failed plan — it frees uninitialised pointers.
    lib.cavacore_free_failed.restype = None
    lib.cavacore_free_failed.argtypes = [c_void_p]

    return lib


_lib: ctypes.CDLL | None = None


def _get_lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _load_lib()
    return _lib


def is_cavacore_available() -> bool:
    """Return True only if the native library is present and loads successfully.

    Performs a real availability check: verifies the .so exists, loads it, and
    confirms required symbols resolve.  Import success alone is not sufficient
    because the library is loaded lazily on first use.
    """
    try:
        if not _SO_PATH.exists():
            return False
        lib = _get_lib()
        # Spot-check that the three required symbols resolve.
        _ = lib.cava_init
        _ = lib.cava_execute
        _ = lib.cava_destroy
        return True
    except Exception:
        return False


SCALING_LINEAR = 0
SCALING_DECIBEL = 1

# Upstream CAVA defaults
_DEFAULT_NOISE_REDUCTION = 0.77
_DEFAULT_LOW_CUT_OFF = 50
_DEFAULT_HIGH_CUT_OFF = 10000


class CavaCoreBackend:
    """Thin Python wrapper around upstream cavacore.

    One instance corresponds to one cava_plan.  Not thread-safe: call execute()
    from a single thread only.  Call close() (or use as a context manager) when
    done to release fftw3 memory.

    Parameters
    ----------
    n_bars:
        Number of output bars per channel.  For 48 kHz, maximum is 2049.
    rate:
        Sample rate in Hz.  Must match the canonical PCM rate (48000).
    channels:
        Number of audio channels (1 = mono, 2 = stereo).
    autosens:
        CAVA automatic sensitivity adjustment.  1 = on (output in [0, 1]);
        0 = off (output is raw, unbounded).
    noise_reduction:
        CAVA noise reduction [0, 1].  0.77 is the upstream default.
    low_cut_off:
        Lower cutoff frequency in Hz.  CAVA default: 50.
    high_cut_off:
        Upper cutoff frequency in Hz.  CAVA default: 10000.
    scaling_mode:
        SCALING_LINEAR (0) — samples are used as-is by cavacore after the
        32768 amplitude scale applied at this boundary.
        SCALING_DECIBEL (1) — cavacore divides by 32768 internally; incompatible
        with the boundary scaling and not used by HueSync.
    """

    def __init__(
        self,
        *,
        n_bars: int,
        rate: int,
        channels: int,
        autosens: int = 1,
        noise_reduction: float = _DEFAULT_NOISE_REDUCTION,
        low_cut_off: int = _DEFAULT_LOW_CUT_OFF,
        high_cut_off: int = _DEFAULT_HIGH_CUT_OFF,
        scaling_mode: int = SCALING_LINEAR,
    ) -> None:
        # Initialise sentinel so __del__ is safe if __init__ raises at any point.
        self._plan: int | None = None
        self._lib: ctypes.CDLL | None = None

        lib = _get_lib()
        self._lib = lib

        plan = lib.cava_init(
            n_bars,
            rate,
            channels,
            autosens,
            noise_reduction,
            low_cut_off,
            high_cut_off,
            scaling_mode,
        )

        if plan is None:
            raise RuntimeError("cava_init returned NULL — out of memory?")

        status = lib.cavacore_status(plan)
        if status != 0:
            err_bytes = lib.cavacore_error(plan)
            err = err_bytes.decode(errors="replace") if err_bytes else "(no message)"
            # Use cavacore_free_failed, NOT cava_destroy: on the error path cava_init
            # returns early after malloc()-ing only the plan struct; inner audio buffers
            # and fftw_plan pointers are uninitialised.  cava_destroy() would call
            # free() on garbage pointers — undefined behaviour.
            lib.cavacore_free_failed(plan)
            raise RuntimeError(f"cava_init failed (status={status}): {err}")

        # Only set _plan after verifying the plan is fully initialised.
        self._plan = plan
        self._n_bars: int = lib.cavacore_n_bars(plan)
        self._channels: int = lib.cavacore_channels(plan)

        # Pre-allocate output buffer; reused every execute() call.
        self._out_buf = (ctypes.c_double * (self._n_bars * self._channels))()

        # Scheduler: carry buffer for deterministic 480-frame execution blocks.
        # Stored as a 1-D float64 array of interleaved samples (length is always
        # a multiple of channels), or None when empty.
        self._carry_flat: np.ndarray | None = None
        self._exec_block_flat: int = _EXEC_BLOCK_FRAMES * self._channels

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def n_bars(self) -> int:
        return self._n_bars

    @property
    def channels(self) -> int:
        return self._channels

    def execute(self, samples: np.ndarray) -> np.ndarray | None:
        """Feed samples through the carry-buffer scheduler and return bar values.

        Samples are collected in a carry buffer and processed in deterministic
        480-frame execution blocks.  Returns the bar array from the last
        processed block, or None if the carry buffer has not yet accumulated
        enough samples for a complete block.

        Parameters
        ----------
        samples:
            For stereo (channels=2): shape (n_frames, 2), dtype float32 or float64,
            range approximately [-1.0, 1.0].  Columns are [L, R].
            For mono (channels=1): shape (n_frames,) or (n_frames, 1).
            Canonical HueSync PCM is converted to cavacore's int16 amplitude
            range (×32768) at this boundary; the caller's array is not modified.

        Returns
        -------
        np.ndarray or None
            shape (n_bars,), dtype float64.  None if no complete 480-frame block
            was available.  With autosens=1 values are in [0, 1].
            For stereo, bars are the average of L and R channels.
        """
        if self._plan is None:
            raise RuntimeError("CavaCoreBackend has been closed")

        # Scale canonical [-1,1] float32 to cavacore's expected int16 amplitude
        # range (~[-32768, 32768]).  Creates a new array; caller's samples unchanged.
        incoming_flat = (
            np.ascontiguousarray(samples, dtype=np.float64).flatten() * _PCM_SCALE
        )

        # Prepend carry from previous call.
        if self._carry_flat is not None:
            buf = np.concatenate([self._carry_flat, incoming_flat])
        else:
            buf = incoming_flat

        result: np.ndarray | None = None
        block = self._exec_block_flat
        offset = 0

        while offset + block <= len(buf):
            # Extract exactly one execution block and pass to cavacore.
            chunk = np.ascontiguousarray(buf[offset : offset + block])
            c_in = chunk.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
            self._lib.cava_execute(c_in, len(chunk), self._out_buf, self._plan)
            raw = np.frombuffer(self._out_buf, dtype=np.float64).copy()
            if self._channels == 2:
                result = (raw[: self._n_bars] + raw[self._n_bars :]) / 2.0
            else:
                result = raw[: self._n_bars].copy()
            offset += block

        # Save remainder as carry for the next call.
        remainder = buf[offset:]
        self._carry_flat = remainder if len(remainder) > 0 else None

        return result  # None when no complete block was processed

    def close(self) -> None:
        """Release the cava_plan and fftw3 resources.  Idempotent."""
        if self._plan is not None and self._lib is not None:
            self._lib.cava_destroy(self._plan)
            self._plan = None

    def __enter__(self) -> CavaCoreBackend:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Safe even if __init__ raised before _plan was set.
        self.close()
