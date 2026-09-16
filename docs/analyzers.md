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

## Extension status

LoudnessAnalyzer and ChromaAnalyzer are protocol extension points, not implemented
selectable production algorithms. Their future processors can coexist with Spectrum
and Beat and consume shared PCM/magnitudes. No family is implicitly mutually exclusive.
`BeatAlgorithm`, `LoudnessAlgorithm` and `ChromaAlgorithm` in conceptual diagrams name
family implementations; do not infer that all are concrete shipped classes.

## Legacy external CAVA

`bars_source=cava` selects the LMS external process/FIFO route. It can have its legacy
PCM/onset/HPSS tap; those controls do not imply a canonical HPSS/Loudness processor.
`use_hpss_separation` is a legacy tap setting, not implementation of the future
canonical families. External FIFO is outside ENGINES and incompatible with an
embedded `cavacore` request.
