# Analyzers and algorithms

`Analyser` stores shared analysis configuration; it is not an Effect. A runtime
Profile carries its settings into the common canonical pipeline.

## Spectrum family

SpectrumProcessor wraps one registered SpectrumEngine.

| Engine | Input / DSP | Availability |
|---|---|---|
| `v2` | Shared 2048/Hamming STFT; logarithmic bands, max aggregation, squelch, peak EMA normalization, clamp and per-bar falloff | Python dependencies |
| `cavacore` | Canonical stereo PCM; upstream 4096/8192 Hann FFTs and native conditioning; 480-frame blocks at 100 Hz | Built/loaded native CAVA + FFTW |

V2 DSP is unchanged by publication/lifecycle fixes. Embedded CAVA receives float64
interleaved PCM scaled by 32768, then returns conditioned left/right bars which the
adapter averages. Upstream temporal algorithms run at HueSync's chosen execution
cadence; numeric identity with a CAVA frontend at another cadence is not promised.

## Beat family

BeatDetector independently wraps the combined, multiband or Superflux onset path.
It receives shared STFT analysis regardless of whether Spectrum is ready. Rebuild,
feed and reset synchronize access so old results cannot be parsed using new method
state. Settings include onset_method/delta/alpha, superflux_mu/lag and bass/mid bounds.

Delayed Beat publications retain their audio intervals and can carry historical
Spectrum bars with explicit provenance. See [publication policy](audio-pipeline.md).

## Loudness family

`KWeightedLoudnessAnalyzer` implements the `LoudnessAnalyzer` protocol alongside
Spectrum and Beat in every canonical pipeline. Its standalone pure-NumPy meter
uses the published ITU-R BS.1770-5 48 kHz K-weighting coefficients, stereo channel
weights and −0.691 offset. Momentary (400 ms) and short-term (3 s) windows retain
filter/window state across chunks. There is no integrated-programme gating.

Reporting uses the same 480-sample hop intervals as Beat. Each reading includes
PCM through that shared STFT window's completion; no later PCM is used to compute
an earlier hop. Remaining PCM is consumed without inventing padded EOS readings.
Reset clears both filters and windows. Values are `None` during warmup and `-inf`
at silence/below −70 LUFS; WebSocket JSON maps these to `null`.

Now Playing displays momentary LUFS beside Energy blend. This is observation only:
`SustainedEnergyTracker` and `LayerMixer` retain their existing blend behavior.
CAVA can have newer Spectrum intervals than shared-hop loudness; preview reads the
latest loudness PublicationRecord separately without rewinding the Effects snapshot.
The legacy external CAVA/FIFO route does not run this canonical processor.

## Extension status

ChromaAnalyzer remains a protocol extension point without a shipped algorithm.
Processor families coexist; Loudness does not replace Spectrum, Beat or sustained
energy. No additional analysis architecture or dependency is introduced.

## Legacy external CAVA

`bars_source=cava` selects the LMS external process/FIFO route. It can have its legacy
PCM/onset/HPSS tap; those controls do not imply a canonical HPSS/Loudness processor.
`use_hpss_separation` is a legacy tap setting, not implementation of the future
canonical families. External FIFO is outside ENGINES and incompatible with an
embedded `cavacore` request.

### Optional canonical colour normalisation

`band_normalise` defaults to false. Enable it to compare each canonical Spectrum
bar with its own rolling average, using the same BandNormaliser as external FIFO.
It applies after either V2 (including pink compensation) or CAVA Core. The existing
Effect `exertion_clip` remains the single clip control; steady bands at clip 3
produce about 0.33 before sensitivity. FIFO always normalises, regardless of this flag.

Switching live does not restart the session. The next fresh spectrum seeds the
EMA; carried historical bars are not processed again. Preview and Effects receive
the same normalised `bars`. Raw-derived scalar aggregates remain unchanged,
including `full`: canonical assembly currently supplies `sustained_energy=None`,
so the default blend uses that raw `full` fallback, not SustainedEnergyTracker.
