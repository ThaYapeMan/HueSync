# Development

Python source is in `src/huesync`, tests in `tests`, the React frontend in `web`.
Read [the frozen architecture](ANALYSIS_ARCHITECTURE.md) before changing analysis.

## Environment

Supported Python range is `>=3.11`; CI covers 3.11, 3.12 and the Debian 13
runtime version, 3.13. For Debian/Ubuntu development hosts, install the shared
minimal native build prerequisites before either editable installation or wheel
building (run from the repository root):

```sh
sudo apt-get update
xargs sudo apt-get install -y --no-install-recommends < scripts/native-build-packages.txt
sudo apt-get install -y python3-venv
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
# Use Node 22.22.2, matching CI and the installer, before this step.
cd web
npm ci
```

For Python source-only tests in an existing environment, pytest sets `pythonpath=src`.
Tests that require the native library may skip; do not relabel them as native passes.
Acceptance uses ffmpeg; install it separately when exercising file decoding.

## Dependency ownership and wheel builds

`scripts/native-build-packages.txt` is the Debian/Ubuntu CAVA build dependency
source shared by CI and the installer: compiler/C headers and FFTW development
files. The Hatch hook compiles and links the native library for both editable and
wheel builds; Python build isolation cannot install these system headers. FFTW's
runtime shared library must also be present when using the resulting wheel.

`pyproject.toml` declares Python runtime dependencies and the separate `dev` extra.
That extra includes matplotlib because DSP tests import the plotting-capable Phase
2A comparison modules. Production installs do not need the dev extra. The optional
`calibration` extra adds soundfile; file acceptance runs may also need ffmpeg.

From the repository root, after the same native prerequisites:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

This invokes the same Hatch build hook as CI and the installer. For installed-artifact
verification, install the wheel into a fresh venv and import from outside the source
tree; pytest deliberately adds `src` and does not prove the installed wheel's native
library loads. For standard deployment use the installer, which additionally provisions
ALSA, codecs, AirPlay, frontend and services. Do not install that full stack merely
to run unit tests on a development host.

When changing dependencies, check every bootstrap consumer: installer, CI, wheel
build, development setup and documentation. The frontend uses `npm ci` and the
committed lockfile, with Node 22.22.2 in CI/installer; Node 20 is insufficient for the
current locked test dependencies. In particular, jsdom 30.0.1 requires
`^22.22.2 || ^24.15.0 || >=26.0.0`; installer and CI pin the same compatible
patch release. See [testing](testing.md).

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
