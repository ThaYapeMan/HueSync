# HueSync

Music-reactive Philips Hue Entertainment lighting from LMS or AirPlay 2.
HueSync analyzes live audio, produces generic audio features, and renders Effects
through a Hue Entertainment output driver. No microphone or precomputed BPM tags
are required.

## HueSync domain model

HueSync has two architectural levels: a **product/domain model** describing what
runs together, and an **audio-analysis subsystem** describing how audio becomes
features. The frozen canonical pipeline is a subsystem within the broader model.

### Six core entities

| Entity | Role |
|---|---|
| **VirtualPlayer** | The player/audio-source integration boundary. LMS/Squeezelite and AirPlay are implemented. It holds source identity and connection settings, not Spectrum, Beat or Effects logic. |
| **Controller** | The physical lighting controller and its connection credentials. The current runtime output is Philips Hue Bridge; a Controller is separate from a Zone. |
| **Zone** | A logical group of lights controlled together, currently mapped to a Hue Entertainment Area on its Controller. Formerly **LightProvider**. |
| **Analyser** | Reusable analysis configuration: Spectrum engine selection, bands/cutoffs and onset algorithm/settings. It is source-independent. Formerly **AnalysisConfig**. |
| **EnergyProfile** | Selects high- and low-energy Effects and controls their blend thresholds and response smoothing. An empty low-energy selection uses the high-energy Effect. Formerly **Crossfader**. |
| **Effect** | A visual algorithm and its settings, consuming generic `AudioFeatures`. Current examples include `spectrum_rgb`, `wave` and `swirl`. |

Spectrum and Beat are independent processor families with separately configured
algorithms; families can coexist. This does not mean every family already has a
UI enable switch: Loudness and Chroma currently expose extension protocols only.
Effect settings also supply the current bass/mid feature boundaries to runtime
analysis; the six-entity model is a conceptual division, not a claim that every
analysis-related setting has already moved into Analyser.

### Coupling and runtime composition

**Coupling binds the configured entities into the session that actually runs.**
It stores four direct references: `player_id`, `analyser_id`, `zone_id` and
`energy_profile_id`. The Zone references its Controller through `controller_id`;
the EnergyProfile references its Effects through `high_energy_effect_id` and
`low_energy_effect_id`. Controller and Effect are therefore bound indirectly.

```text
HueSync domain model
    ├── VirtualPlayer ───────────────────┐
    ├── Analyser ────────────────────────┤
    ├── Zone → Controller ───────────────┤
    └── EnergyProfile → Effect(s) ───────┤
                                        ▼
                                     Coupling
                                        ▼
                              Running HueSync session
                                ├── player / ingress
                                ├── analysis
                                ├── energy blending
                                ├── effects
                                └── controller / zone output
```

The six core entities plus Coupling are persisted configuration objects. Activation
resolves their references and builds an internal runtime Profile; that Profile is
not an additional user-facing domain entity. Historical names above are migration
terminology, not current entity names. EnergyProfiles still use the JSON collection
key `crossfaders` for storage compatibility. See [configuration](docs/configuration.md).

## Player-independent audio architecture

**LMS/Squeezelite and AirPlay are current integrations. Neither defines HueSync's
architecture. The player-specific boundary ends at canonical audio ingress.**
Once audio has been canonicalised, downstream analysis and Effects do not need to
know which player supplied it.

### Player/source support in production code

“IMPLEMENTED” describes code presence, not completed target-LXC validation.

| Player / source | Status | Ingress | Uses canonical analysis | Notes |
|---|---|---|---|---|
| LMS / Squeezelite | IMPLEMENTED | Stereo shared memory from patched Squeezelite SHM v1 | Yes, with `bars_source=pcm_pipeline` | Also supports a separate external CAVA/FIFO compatibility route. |
| AirPlay | IMPLEMENTED | shairport-sync → S16_LE stereo, 44.1 kHz named pipe → `AirPlayPipeStereoSource` | Yes, always | Same canonical pipeline factory and downstream Effects as LMS PCM. |
| Sonos (dedicated integration) | NOT PRESENT | — | — | Potential future adapter. A Sonos player exposed through a third-party LMS plugin can be followed through the LMS integration; this is not a native Sonos ingress. |
| Roon | NOT PRESENT | — | — | Potential future adapter; no production Roon player type or ingress. |

No other production VirtualPlayer types are currently defined. AirPlay uses one
managed global shairport-sync instance and `/run/huesync/airplay.pcm`, with one
reader; it does not create an independent receiver per configured VirtualPlayer.
LMS SHM continuity and AirPlay pipe framing/disconnect events belong to their
respective ingress adapters. Both canonical routes retain stereo channels and
use the shared canonicalizer, including conversion to 48 kHz canonical PCM.

### Internal analysis subsystem

```text
LMS / Squeezelite ─┐
AirPlay ───────────┤
future player ────┤
                  ▼
        player-specific ingress
                  ▼
             canonical PCM
                  ▼
     CanonicalAnalysisPipeline
                  ▼
        SharedAnalysisFrame
                  ▼
        AnalysisProcessor[]
          ├── SpectrumProcessor → SpectrumEngine → V2 / CAVA Core / future
          ├── BeatDetector      → BeatAlgorithm (onset implementation)
          ├── LoudnessAnalyzer  → extension protocol; future algorithm
          └── ChromaAnalyzer    → extension protocol; future algorithm
                  ▼
         PublicationRecord
                  ▼
           AudioFeatures
                  ▼
               Effect
                  ▼
      Scene → controller / zone output
```

Canonicalization runs inside `CanonicalAnalysisPipeline`; the diagram shows the
logical audio boundary. The pipeline owns shared analysis, processor orchestration,
epoch/sample clocks, EOS/invalidation, publication and its worker lifecycle.
PlayerManager owns the physical source and session, retaining the source until
all readers terminate.

`SharedAnalysisFrame` carries canonical PCM, shared STFT magnitudes and sample
positions to the enabled processors. Processors may coexist; the current worker
dispatches them sequentially. `SpectrumProcessor` wraps a `SpectrumEngine`;
`BeatDetector` is independent of that engine. V2 and Beat reuse shared STFT work.
CAVA Core receives canonical stereo PCM and owns its own FFTs and conditioning.
The algorithm labels describe family responsibilities; Loudness/Chroma are not
shipped selectable analyzers.

`PublicationRecord` binds features to their actual audio interval, epoch and
contributors. Delayed results remain in delivery-order publications; the live
`latest()` snapshot does not move backward in audio time. Effects consume generic
`AudioFeatures`, independently of the player or analysis implementation.
See [PCM/lifecycle/publication](docs/audio-pipeline.md) for the precise contract.

The analysis architecture is frozen: static registries/internal factories, with
no generic DSP DAG, dynamic plugin framework or node scheduler.

### Adding another player

A future player integration supplies decoded audio and source lifecycle/continuity
events through the canonical ingress contract. It owns its transport, framing,
channel layout and detection of gaps/restarts; uncertain continuity must invalidate
the old stream before new PCM enters its DSP epoch. The shared pipeline performs
canonicalization and downstream analysis.

Adding a player needs its adapter, player configuration/activation wiring and
integration tests. It does **not** require reimplementing Spectrum, Beat,
`PublicationRecord`, `AudioFeatures`, EnergyProfile, Effects or lighting output
when canonical PCM is supplied. Sonos, Roon and other players are potential
extensions behind this boundary, not promises of shipped support. Different
players may use different transports; no particular pipe, file or shared-memory
mechanism is required. Source-specific behavior must remain at ingress rather
than leaking into Effects. A deliberate derived-bars compatibility route remains
separate from this canonical contract.

## Spectrum engines and legacy CAVA

CAVA has two distinct integration paths:

```text
Canonical: VirtualPlayer / ingress → canonical PCM → SpectrumProcessor
                                                   → SpectrumEngine → CAVA Core
Legacy:    LMS → squeezelite → external CAVA process → FIFO derived bars
                                                   → legacy analysis → AudioFeatures
```

| Route | Selection | Analysis |
|---|---|---|
| LMS canonical PCM | `bars_source=pcm_pipeline`, `spectrum_backend=v2` or `cavacore` | Shared canonical pipeline: Spectrum + Beat; requires patched SHM v1 producer |
| AirPlay canonical PCM | `spectrum_backend=v2` or `cavacore`; set `bars_source=pcm_pipeline` | Same shared canonical pipeline: Spectrum + Beat |
| LMS external bars | `bars_source=cava`, backend default `v2` | External CAVA/FIFO; the backend field does not select embedded processing |

V2 uses the shared STFT. Embedded CAVA Core keeps upstream native DSP and its own
FFTs; HueSync schedules 480-frame executions at 48 kHz (100 Hz). External CAVA/FIFO
is outside the canonical AnalysisProcessor/SpectrumEngine path and registry.
**LMS does not require external CAVA when using canonical PCM.**

`bars_source=cava` with `spectrum_backend=cavacore` is rejected. An explicit
embedded-engine request either activates that engine or fails; it never silently
selects V2 or FIFO. See [analyzers](docs/analyzers.md) and the
[frozen architecture](docs/ANALYSIS_ARCHITECTURE.md).

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
