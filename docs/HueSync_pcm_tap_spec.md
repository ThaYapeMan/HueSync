# HueSync — direct PCM tap

Read squeezelite's shared memory directly and run our own STFT, instead of
consuming cava's reduced bars for onset detection.

**cava keeps running and keeps driving colour.** This adds a second reader on
the same shared memory; it does not replace anything.

---

## Why

Onset detection currently runs on cava's output, which is the wrong input for it:

| | cava gives us | Dixon / SuperFlux want |
|---|---|---|
| Frame rate | ~30 Hz (33 ms) | 100 Hz (10 ms hop) |
| Resolution | 24–60 log-spaced bars | 1024 FFT bins |
| Smoothing | already applied (cava is built to look good) | none — raw magnitudes |

Dixon's `w = 3` local-maximum window is ±30 ms at 100 Hz, which is right for
musical onsets. At 30 Hz the same `w` spans ±100 ms and merges separate drum
hits. And SuperFlux's `mu = 3` bin maximum filter is meaningless when there are
only 30 bins covering the whole spectrum.

Both new onset methods would be evaluated on data that cannot support them.

---

## The shared memory format — verified from source

From `output_vis.c` in ralph-irving/squeezelite:

```c
#define VIS_BUF_SIZE 16384

static struct vis_t {
    pthread_rwlock_t rwlock;
    u32_t  buf_size;
    u32_t  buf_index;
    bool   running;
    u32_t  rate;
    time_t updated;
    s16_t  buffer[VIS_BUF_SIZE];
} *vis_mmap;
```

Path: `/dev/shm/squeezelite-<mac>` (MAC lowercase with colons).

### What this tells us

- **16-bit signed samples, interleaved stereo.** The writer does
  `buffer[i++] = L; buffer[i++] = R;` per frame.
- **Circular buffer**, 16384 samples = 8192 stereo frames. At 44.1 kHz that is
  **186 ms** of audio — matching the "signal envelope of around 0.18s" a forum
  user measured. Plenty for a 2048-sample (46 ms) window.
- **`buf_index`** is the write position in *sample* units (not frames), wrapping
  at `VIS_BUF_SIZE`.
- **`rate`** is the current sample rate. **Use this** — the hop size must be
  10 ms *regardless* of whether LMS sends 44.1 or 48 kHz, and the frequency
  mapping depends on it. cava is currently told nothing about this.
- **`running`** goes false on silence; **`updated`** is a `time_t` for staleness
  detection.
- **ReplayGain is applied, volume is not.** Good for us: levels stay consistent
  regardless of the volume setting.

### Header size

Computed for glibc x86-64 (`pthread_rwlock_t` = 56 bytes, with alignment
padding):

```
offset  0  pthread_rwlock_t   56 bytes
offset 56  buf_size            4
offset 60  buf_index           4
offset 64  running             1  (+3 padding)
offset 68  rate                4  (+4 padding for time_t alignment)
offset 72  updated             8
offset 80  buffer[]        32768
                          -------
                           32848 bytes
```

That matches the ~33 KB file size people report. **Verify this empirically
rather than trusting the arithmetic** — `pthread_rwlock_t` size is
platform-specific. A simple check: the file size on disk must equal
`header + 32768`.

### The lock — deliberately not taken

The struct starts with a `pthread_rwlock_t`, but **do not try to acquire it from
Python.**

Two reasons. First, taking a read lock can make the writer's `trywrlock` fail,
and squeezelite then *skips exporting that block entirely* — so locking politely
would cause the gaps we are trying to avoid. Second, pthread locks in shared
memory are awkward to use correctly from Python.

Read without the lock. A torn read means a handful of samples from the wrong
position in a 2048-sample window — inaudible in an FFT magnitude, and rare.
Correctness is not at stake; this is audio for analysis, not for playback.

---

## Implementation

### New `AudioSource`

```python
class SqueezeliteShmSource:
    """Reads raw PCM from squeezelite's visualiser shared memory."""

    def open(self, mac: str) -> None: ...      # mmap /dev/shm/squeezelite-<mac>
    def read_new(self) -> np.ndarray: ...      # mono float32, samples since last call
    @property
    def sample_rate(self) -> int: ...          # from the header
    @property
    def running(self) -> bool: ...             # from the header
```

Track the previously seen `buf_index`; each poll, return everything written
since. Handle wraparound. If more than `VIS_BUF_SIZE` has been written since the
last read (we fell behind), return the most recent window and log a warning —
falling behind means the poll loop is too slow, which is worth knowing.

Downmix interleaved stereo to mono: `(L + R) / 2`, as float32 scaled to
-1.0…1.0.

### STFT

```
window     Hamming, 2048 samples
hop        round(sample_rate * 0.010)     # 10 ms — 441 at 44.1 kHz, 480 at 48 kHz
magnitude  linear (not log — Dixon tested both, linear won)
```

Keep a rolling input buffer so window boundaries are independent of how much
each poll returns.

Poll at ~100 Hz. That is well within budget: a 2048-point real FFT 100×/second
is a fraction of a percent of a core with NumPy. The cost here is plumbing, not
arithmetic.

### Feeding the detectors

The existing onset methods move to this input:

- **`combined`** — flux summed over all bins, then Dixon's three conditions
- **`multiband`** — bins grouped into bass/mid/treble by *frequency* (now
  possible, since we know `rate`), flux and peak-picking per band
- **`superflux`** — triangular bands, maximum filter over `mu` neighbouring
  bands, difference against frame `n − lag`

cava's bars continue to drive colour. Nothing about the colour path changes.

### Where it slots in

```
squeezelite shm ─┬─→ cava              → bars      → colour   (unchanged)
                 └─→ SqueezeliteShmSource → STFT   → onsets
```

Both read the same segment. It is read-only for us and multiple readers are
fine.

---

## Fallback

If the shared memory cannot be opened (wrong MAC, squeezelite started without
`-v`, permissions), fall back to the current cava-based onset detection and say
so in the GUI status. Do not fail activation over it — the colour path still
works without onsets.

Reuse `_wait_for_shm()`: it already polls for the segment's existence before
starting cava, and the same wait applies here.

---

## Tests

- Header parsing against a synthetic `vis_t` written by the test itself
- Wraparound: write past `VIS_BUF_SIZE` and check the returned samples are
  contiguous and in order
- Falling behind: write more than the buffer holds between reads, check it
  returns the newest window and warns
- Hop size follows `rate`: 441 at 44100, 480 at 48000
- STFT output shape and that a known sine lands in the expected bin

## Order

1. `SqueezeliteShmSource` with header parsing and wraparound — testable on its
   own, no audio needed
2. STFT layer producing magnitude frames at 100 Hz
3. Point the existing `combined` onset detector at it, verify against cava's
   version that onsets still land where expected
4. Only then add `multiband` and `superflux`

Step 3 is the checkpoint: if `combined` behaves worse on the new input than on
cava's bars, something is wrong in the plumbing and it is better to find that
before two more methods are stacked on top.
