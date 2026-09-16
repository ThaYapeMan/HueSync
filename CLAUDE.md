# HueSync repository guidance

## Authority and scope

The analysis architecture is frozen. Read [README.md](README.md),
[ANALYSIS_ARCHITECTURE.md](docs/ANALYSIS_ARCHITECTURE.md) and
[audio-pipeline.md](docs/audio-pipeline.md) for implemented contracts.
Use [development.md](docs/development.md) and [testing.md](docs/testing.md)
for working and validation instructions.

Top-level `docs/HueSync_*.md` files bearing a historical banner, `docs/archive/`
and local untracked research files are **not current implementation instructions**.
Do not implement `docs/future/` proposals unless asked.

## Conventions

- Code comments, docstrings, commit messages and documentation are English.
- Do not add AI attribution to commits.
- Run the full Python suite, Ruff and relevant frontend tests before reporting
  validation. Report skipped tests and warnings. A subset is not the full suite.
- Keep unrelated local artifacts out of commits.
- Do not propose a DSP DAG, plugin framework, new processor hierarchy or broad
  analysis redesign. Correct defects within the existing boundaries.

## Boundaries to preserve

- CanonicalAnalysisPipeline owns canonicalization, shared analysis, processor
  dispatch, epoch/sample clocks, EOS/invalidation and publication.
- PlayerManager owns physical sources and sessions. It cannot close a source
  while an analysis worker can still use it. Incomplete teardown retains the
  session and requires a safe retry.
- SpectrumProcessor wraps SpectrumEngine. BeatDetector is independent of it.
- Effects consume AudioFeatures and return Scenes; drivers map Scenes to hardware.
  Hue-specific commands belong in the Hue output implementation, not Effects.
- External CAVA/FIFO is separate from canonical PCM analysis.
- Registry definitions, not player-specific branches, own engine construction.

## Operational constraints

LMS playback uses a paced audio device. `snd-dummy` is provisioned on the host and
passed into the LXC; an unpaced ALSA null device can decode faster than realtime.
External FIFO readers must start before their CAVA writer. Canonical LMS requires
SHM v1 and the pinned producer build; do not fall back to legacy mono/v0 semantics.

See [deployment-lxc.md](docs/deployment-lxc.md) before deployment. The dev environment
may have Python/toolchains; inspect it rather than assuming it lacks them.
Native runtime/performance claims require target evidence, not green stub tests.

The repository installer (`scripts/install-huesync.sh`) owns standard deployment,
dependencies, builds and explicit persisted-schema migration. Current runtime accepts
schema version 1 only; historical conversions belong exclusively to migration code.
See [cutover inventory](docs/compatibility-cutover.md). Never add runtime fallbacks
for historical entity names or regenerate tracked commit constants.
