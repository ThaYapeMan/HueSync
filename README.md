# HueSync

Music-reactive Philips Hue Entertainment lighting from LMS or AirPlay 2.
HueSync analyzes live audio, produces generic audio features, and renders Effects
through a Hue Entertainment output driver. No microphone or precomputed BPM tags
are required.

## Current analysis architecture

```text
Audio ingress
    ↓
CanonicalAnalysisPipeline
    ↓
SharedAnalysisFrame
    ↓
AnalysisProcessor[]
    ├── SpectrumProcessor → SpectrumEngine → V2 / CAVA Core / future
    ├── BeatDetector      → onset algorithm
    ├── LoudnessAnalyzer → extension protocol (no production algorithm yet)
    └── ChromaAnalyzer   → extension protocol (no production algorithm yet)
    ↓
PublicationRecord → AudioFeatures → Effects → Scene → output driver
```

The architecture is frozen. Processors can be enabled together; the pipeline
calls them sequentially on one worker. Spectrum and Beat are separate feature
families. V2 and Beat reuse shared STFT analysis. Embedded CAVA Core receives
canonical stereo PCM and performs its own FFT and conditioning.

Legacy external CAVA is a **separate derived-bars path**:

```text
LMS → squeezelite → external CAVA → FIFO bars → legacy analysis → AudioFeatures
```

It is not an entry in the canonical Spectrum engine registry.

## Supported routes in code

| Audio ingress | Selection | Analysis |
|---|---|---|
| AirPlay PCM | `spectrum_backend=v2` | Shared canonical pipeline, V2 Spectrum + Beat |
| AirPlay PCM | `pcm_pipeline` + `cavacore` | Shared canonical pipeline, embedded CAVA + Beat |
| LMS PCM | `bars_source=pcm_pipeline`, engine `v2` or `cavacore` | Same canonical pipeline; requires patched Squeezelite SHM v1 |
| LMS external bars | `bars_source=cava`, backend default `v2` | External CAVA/FIFO; the backend field does not select embedded processing |

`bars_source=cava` with `spectrum_backend=cavacore` is rejected. An explicit
embedded-engine request either activates that engine or fails; it never silently
selects V2 or FIFO. AirPlay always uses canonical PCM; use `pcm_pipeline` for its
Analyser configuration to make intent clear.

Loudness and Chroma are extension contracts, not implemented selectable analyzers.
No generic DSP graph, dynamic plugin loader or node scheduler is present.

## Features

- Live Spectrum and onset-driven lighting, with independent Beat configuration.
- Reusable VirtualPlayer, Controller, Zone, Analyser, Effect, EnergyProfile and
  Coupling configuration.
- Two-layer rendering and output-independent Effects/Scenes.
- Web UI and API, plus an offline acceptance runner with sample-derived records.
- Explicit canonical stream epochs, clean EOS, invalidation and source ownership.

## Quick start

A fresh Linux installation needs Python 3.11+, compiler tools and FFTW development
headers because the package build compiles the embedded library even when V2 is
selected. LMS canonical PCM also needs the patched producer; installing a stock
Squeezelite package is insufficient for that route.

Follow [installation](docs/installation.md), then [LXC deployment](docs/deployment-lxc.md)
for the target host. The UI listens on port **8420**. Configure a controller and
Entertainment zone, an audio source, an Analyser and an Effect/EnergyProfile,
then activate their Coupling. See [configuration](docs/configuration.md).

## Validation status

**CODE-VERIFIED** means inspected code and reproducible local tests. It does not
mean the current binary has been deployed or timed on the target.

**REQUIRES LXC VALIDATION:** native CAVA/FFTW execution and leak stress, full target
Squeezelite link, live producer/SHM integration, clean wheel installation,
realtime backlog and visual V2/CAVA comparison. No such validation is claimed
by this documentation. Earlier benchmark/review documents are historical evidence
for their stated commits only.

## Documentation

- [Frozen architecture](docs/ANALYSIS_ARCHITECTURE.md)
- [PCM, lifecycle and publication](docs/audio-pipeline.md)
- [Analyzers and algorithms](docs/analyzers.md)
- [Configuration](docs/configuration.md) · [API operations](docs/api.md)
- [Installation](docs/installation.md) · [LXC deployment](docs/deployment-lxc.md)
- [Development](docs/development.md) · [Testing and acceptance](docs/testing.md)
- [Effects and output boundaries](docs/EFFECT_ENGINE.md)
- [Squeezelite build and SHM ABI](squeezelite/README.md)

Historical prompts/specifications are labelled as historical or stored under
`docs/archive/`. They do not override these references. `docs/future/` contains
unimplemented proposals, not current capabilities.
