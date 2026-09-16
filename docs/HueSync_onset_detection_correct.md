> **Historical document — not the current implementation contract.**
> This preserves an earlier proposal, review or measurement. Do not execute its
> migration/build instructions as current guidance. See [the frozen architecture](ANALYSIS_ARCHITECTURE.md),
> [configuration](configuration.md) and [testing](testing.md). Measurements apply
> only to their original commit/environment; current LXC validation is pending.

# HueSync — onset detection done correctly

The detection function we chose is right. The peak-picking is wrong, and that
is where the accuracy actually comes from.

Source: Simon Dixon, *Onset Detection Revisited*, DAFx-06.
https://www.ofai.at/papers/oefai-tr-2006-12.pdf

---

## Spectral flux is the correct choice — this is settled

Dixon evaluated eight onset detection functions against two datasets: 1060
hand-labelled onsets across instrument families, and 106,054 onsets from
computer-monitored piano performances.

| Function | F-measure (large set) | Mean timing error |
|---|---|---|
| **Spectral flux (SF)** | **0.964 ± 0.017** | **8.8 ms** |
| Complex domain (CD) | 0.966 ± 0.015 | 12.8 ms |
| Rectified complex domain (RCD) | 0.955 ± 0.018 | 9.3 ms |
| Normalised weighted phase deviation | 0.944 ± 0.021 | 10.3 ms |
| Weighted phase deviation | 0.912 ± 0.028 | 9.6 ms |
| Phase deviation (PD) | 0.677 ± 0.044 | 19.5 ms |

His conclusion: the differences between the best algorithms are not
significant, so the choice can be made on *simplicity of programming, speed of
execution and accuracy of correct onsets* — **all of which favour spectral
flux**. It also has the lowest timing error of the lot, which matters more for
lighting than for transcription.

So: no need to look further. The question is how to implement it properly.

---

## Exact parameters from the paper

```
Window:      Hamming
Window size: N = 2048   (46 ms at 44.1 kHz)
Hop size:    h = 441    (10 ms, 78.5 % overlap)
Frame rate:  100 Hz
```

```
SF(n) = Σ_k  H( |X(n,k)| − |X(n−1,k)| )

where H(x) = (x + |x|) / 2      # half-wave rectifier
```

Two details the paper is explicit about, both easy to get wrong:

- **L1 norm, not L2.** "Empirical tests favoured the use of the L1-norm here
  over the L2-norm" used in earlier work.
- **Linear magnitude, not logarithmic.** Linear beat the logarithmic /
  normalised variant proposed by Klapuri.

---

## The peak-picking algorithm — this is what we got wrong

Our current implementation fires when `flux > rolling_mean + k * rolling_std`,
with a fixed cooldown. That is not what Dixon does, and the difference is not
cosmetic: he notes his SF implementation outperformed the published SF results
for the same function *"presumably due to a better peak-picking function"*.

### The correct algorithm

First normalise the detection function `f(n)` to **mean 0, standard deviation
1**. Then `f(n)` is an onset only if **all three** conditions hold:

**1. It is a local maximum**

```
f(n) ≥ f(k)   for all k where  n − w ≤ k ≤ n + w
```

with `w = 3`. **We do not do this at all**, which means we currently fire on
the rising edge rather than at the peak — consistently early, and prone to
multiple triggers on one onset.

Note this requires looking *forward* w frames, so the detector is inherently
w frames (30 ms at 100 Hz) behind real time. Irrelevant for lighting.

**2. It exceeds the local mean by a margin, over an asymmetric window**

```
f(n) ≥ ( Σ_{k=n−mw}^{n+w} f(k) ) / (mw + w + 1) + δ
```

with `m = 3`, so the mean is taken over `n−9 … n+3` — **three times as far
back as forward**. The asymmetry is deliberate: it compares the candidate
against what recently preceded it, not against what follows.

`δ` is the tunable sensitivity threshold. This is the parameter to expose in
the GUI.

**3. It exceeds a decaying threshold carried from the previous onset**

```
f(n) ≥ g_α(n−1)

where  g_α(n) = max( f(n),  α·g_α(n−1) + (1−α)·f(n) )
```

This suppresses re-triggering on the decay of a loud onset — it does the job
of our fixed cooldown, but adaptively: a loud hit raises the bar for longer
than a quiet one.

Dixon notes the improvement from `g_α` is marginal *provided a suitable δ is
chosen*, so this is the one to implement last if effort is limited.

---

## Consequence: the frame rate is a problem

Dixon's parameters assume a **100 Hz frame rate** (10 ms hop). At that rate
`w = 3` means a ±30 ms local-maximum window — appropriate for musical onsets.

HueSync currently works on cava frames arriving at roughly 30 Hz. At that rate
`w = 3` spans ±100 ms, which is far too coarse: separate drum hits would be
merged, and the timing error would be an order of magnitude worse than the
8.8 ms the algorithm is capable of.

Worse, cava's bars are a *reduced* representation (24-60 log-spaced bins) of a
spectrum that has already been smoothed. Spectral flux on smoothed, reduced
data is not the same measurement the paper evaluated.

**This points at the PCM tap.** Reading `/dev/shm/squeezelite-<mac>` directly
and running a 2048-point STFT at 10 ms hop gives the algorithm the input it was
designed for. cava can continue to supply the spectrum for colour; the onset
detector should have its own path.

Cost estimate: a 2048-point real FFT 100 times per second is trivial for NumPy
— well under 1 % of a core. The cost is in the plumbing, not the arithmetic.

---

## The bigger point: audio input should be pluggable

Two separate observations converge here.

**First**, the sync problem is currently unmeasurable. The lights are driven
from the LMS stream, while the sound arrives via sonos-squeezebox (throttled
~2 s ahead) plus Sonos's own buffering. There is no local squeezelite client
with immediate output to compare against, so `light_delay_ms` cannot be
calibrated by ear.

**Second**, this is exactly how the iPhone apps (Light DJ, Hue Essentials)
work: they take audio from the **microphone**.

That is not a limitation — for synchronisation it is a decisive advantage. A
microphone in the room hears precisely what the listener hears, *including*
every millisecond of Sonos buffering. Drive the lights from that and the sync
problem does not need solving, because it does not exist.

### Proposed abstraction

Mirroring the `Output` abstraction already in place:

```python
class AudioSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def read(self) -> np.ndarray:
        """Most recent PCM samples. Never blocks."""
    @property
    def sample_rate(self) -> int: ...
```

Implementations:

| Source | Latency vs. what you hear | Notes |
|---|---|---|
| `SqueezeliteSource` | ahead by the player's buffering | current behaviour; tied to LMS/slimproto |
| `AlsaCaptureSource` | **zero** — it *is* what you hear | USB microphone or line-in |
| `FileSource` | n/a | for tests, no hardware needed |

The ALSA capture route fits the existing deployment neatly: a USB microphone
is passed into the LXC through `/dev/snd`, exactly as `snd-dummy` already is.
Same mechanism, one more device node.

It also decouples the project from slimproto entirely. HueSync becomes "make
lights react to audio", with LMS as one possible source rather than a
requirement — which is the conceptually right shape, and the reason the
phone apps work with any audio source at all.

### Trade-offs, honestly

- A microphone picks up room noise, conversation and reflections. Onset
  detection on a mic feed is measurably harder than on a clean signal.
- Level varies with position and volume; AGC (which `BandNormaliser` already
  does) becomes essential rather than nice to have.
- It requires hardware in the room, near the speakers.

For a room where the lights and the speakers are in the same space — which is
the whole premise — those are acceptable. And it is what the established apps
do.

---

## Recommended order

1. **Fix the peak-picking** to Dixon's three conditions on the existing cava
   feed. Even at 30 Hz this is a real improvement over mean+std with no local
   maximum test, and it costs nothing but code.
2. **Add the PCM tap** and run the flux detector on a proper 2048/441 STFT at
   100 Hz. This is where the 8.8 ms accuracy actually becomes available.
3. **Introduce `AudioSource`** and add `AlsaCaptureSource`. Test with a USB
   microphone; if onsets track what you hear in the room, the sync problem is
   solved rather than calibrated.

Steps 1 and 2 are worth doing regardless of whether the microphone route is
adopted.

## Sources

- Dixon, *Onset Detection Revisited*, DAFx-06 — algorithm, exact parameters,
  peak-picking, comparative evaluation:
  https://www.ofai.at/papers/oefai-tr-2006-12.pdf
- Bello et al., *A Tutorial on Onset Detection in Music Signals*, IEEE TSAP
  13(5), 2005 — the canonical review Dixon extends
