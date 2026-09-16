# Development

Python source is in `src/huesync`, tests in `tests`, the React frontend in `web`.
Read [the frozen architecture](ANALYSIS_ARCHITECTURE.md) before changing analysis.

## Environment

With compiler/FFTW prerequisites from [installation](installation.md):

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cd web
npm ci
```

For Python source-only tests in an existing environment, pytest sets `pythonpath=src`.
Tests that require the native library may skip; do not relabel them as native passes.
Acceptance uses ffmpeg; install it separately when exercising file decoding.

## Boundaries

New Spectrum engines implement SpectrumEngine and receive a static registry entry
plus tests. New feature-family processors implement AnalysisProcessor. Do not copy
ingress, canonicalization, onset, EOS or publication loops. Effects and output drivers
remain independent of analyzer implementation.

Processor updates describe exact source intervals. A slower processor may return a
historical interval. Sequence orders delivery, and carried bars have explicit provenance.
Do not restore timestamp sorting by dropping valid updates.

Keep authoritative docs linked from README. Label superseded prompts/reviews historical;
future proposals are not production features. Preserve unrelated local artifacts.

For frontend changes, `npm test` runs unit tests; `npm run build` creates the UI assets.
Only rebuild/commit generated assets when frontend changes require it. Backend/docs-only
changes do not require unrelated frontend output updates.

See [testing](testing.md) for full validation and native evidence boundaries.

## Deployment versus development

Use `scripts/install-huesync.sh` for standard Debian deployment; development commands
above are not an alternative operator install procedure. Native/commit build artifacts
are generated outside the checkout. Only a wheel-installed runtime is expected to
report generated deployment commit metadata. A direct source import may report unknown.
The current storage schema is versioned: use isolated current-schema fixtures for
runtime tests and explicit migration functions for historical fixtures.
