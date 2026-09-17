# Installation

**The repository installer is the authoritative standard deployment path.**
Target: Debian 13 / trixie, x86_64, with a booted systemd instance. Other targets
fail explicitly. This is the supported code path; clean-target execution still
requires real LXC validation. Do not confuse local tests with a completed deployment.

```sh
git clone https://github.com/ThaYapeMan/HueSync.git
cd HueSync
sudo ./scripts/install-huesync.sh
```

The initial clone requires Git and access to GitHub. The installer owns all further
package/build knowledge. Use a dedicated HueSync container with outbound HTTPS and
Debian apt repositories. Pairing a Hue Bridge uses the API; player/Zone/Coupling configuration is available
in the UI at `http://<host>:8420`. See [API operations](api.md#controller-setup);
neither requires manual JSON edits.

## What it installs

| Component | Build requirements | Runtime requirements / method |
|---|---|---|
| HueSync | Python venv, build/Hatchling, compiler | Fresh wheel-installed venv; dependencies from pyproject.toml |
| CAVA Core | GCC, libfftw3-dev | Packaged native library plus FFTW runtime pulled by apt |
| Squeezelite | GCC/make/patch, ALSA and codec headers | Pinned patched binary at /usr/local/bin/squeezelite; VISEXPORT mandatory |
| Squeezelite default codecs | FLAC, Vorbis/Ogg, MAD, MPG123, FAAD development packages | Matching shared libraries: PCM/FLAC/Vorbis/MP3/AAC; no optional Opus/FFmpeg/ALAC/resampler flags |
| External CAVA/FIFO | Debian CAVA + same pinned Squeezelite source | `cava` plus dedicated `huesync-squeezelite-fifo` producer; separate derived-bars route |
| AirPlay 2 | Autotools, FFmpeg, crypto/plist/Avahi/soxr/systemd development packages | Pinned shairport-sync + nqptp source builds, Avahi, capabilities, managed FIFO |
| Frontend | Private Node 22.22.2 archive, pinned SHA256; npm ci | Compiled assets in wheel; Node is not a runtime requirement |
| Services | systemd, polkit | huesync user/audio group, repository unit, narrow receiver-restart authorization |

The minimal CAVA build packages come from `scripts/native-build-packages.txt`,
shared with CI and development/wheel builds. The additional deployment package
arrays are in `scripts/install-huesync.sh`. Build headers remain
installed for subsequent updates and `--check`; the installer does not purge packages
that another application might need. No curl-to-shell bootstrap or global pip install
is used. Python package versions follow pyproject constraints (not a fully locked
Python dependency set); each fresh release resolves them again.

## Installation stages and ownership

1. Verify OS/architecture, systemd and clean tracked Git state; lock installation.
2. Provision packages/account; archive the selected Git commit into a temporary tree.
3. Build frontend and native wheel there; install into a fresh release venv.
4. Build/link pinned Squeezelite; stop existing HueSync/audio services before replacing
   binaries. Build pinned AirPlay sources without starting the receiver.
5. Run explicit persisted-data migration from the candidate environment. Back up exact
   original bytes, validate schema/references, then atomically replace the JSON file.
6. Install the existing repository systemd layout and verify artifacts/import/native
   initialization/schema/commit. Switch `/opt/huesync/.venv` to the verified release.
7. Start services and check their status and the local HTTP API.

The venv remains at its original absolute path under `/opt/huesync/releases/`; it is
never relocated. The existing service still executes `/opt/huesync/.venv/bin/huesync`.
A previous real `.venv` directory is archived rather than destroyed. Release directories
are retained for diagnostics; they are not automatically garbage-collected or used as
an automatic cross-schema rollback. Configuration remains `/etc/huesync/config.json`.
No credentials are replaced by defaults.

Failures are explicit and stop the installer. If failure occurs after services were
stopped, they remain stopped: correct the reported cause and rerun the installer.
It does not claim a transaction across apt, native binaries, services and schema changes.
Do not start an old application against newly migrated data. Migration failure leaves
the original file intact and does not start the new runtime.

## Migration

Current persisted schema is **version 1**. See [configuration](configuration.md).
Unversioned entity configurations are converted once, including historical collection
and reference names. Backup:

```text
/etc/huesync/config.json.pre-v1.<SHA256-of-original>.bak
```

Backups are mode 0600. Repeated migration of current data does not rewrite it or make
another backup. Unequal old/new aliases, malformed data, duplicate IDs, dangling
nonempty references or unknown fields fail. No silent winner is chosen. Unrecognized
pre-entity datasets fail with the original intact; they require a deliberate migration
extension, not runtime aliases. Keep the old backup until target acceptance is complete.

## Checks and updates

```sh
sudo ./scripts/install-huesync.sh --check
git pull --ff-only
sudo ./scripts/install-huesync.sh
journalctl -u huesync -u shairport-sync -u nqptp
```

`--check` verifies an **existing target installation**. It requires Debian 13
(trixie), x86_64, with systemd running (`/run/systemd/system`); it is not a
generic CI/WSL/build-host check. It performs no apt/build/migration/service-start operation. It checks current
schema, installed wheel location and Git metadata, binary SHA256/provenance, shared
libraries, native CAVA smoke execution, frontend/FIFO, unit validity and service state.
Use sudo to read private configuration. It does not read live PCM or activate a player.
`scripts/update.sh` is a convenience wrapper for pull plus this same installer.

Installed commit metadata is generated in the wheel from the checked-out revision;
it never rewrites a tracked `_commit.py`. Building from an isolated archive supplies
that revision explicitly. A source-tree import without generated metadata reports
`unknown`; the installed application must match `git rev-parse --short HEAD`.

## Target boundaries

The LXC host must provide paced ALSA devices (normally snd-dummy) for LMS. The installer
adds audio-group membership, but cannot create host devices or change host mappings.
Do not substitute an unpaced null sink. AirPlay uses the managed global shairport-sync
instance; network multicast and timing ports must be available. The installer does not
change unrelated firewall, host-kernel or container configuration.

Native memory stress, live producer continuity, realtime performance and visual A/B
remain **REQUIRES LXC VALIDATION**. See [deployment checklist](deployment-lxc.md).

### Two producer ABIs, one deployment authority

Canonical LMS uses `/usr/local/bin/squeezelite` with HueSync SHM v1 (ring at byte
120). External CAVA expects the upstream SHM ring at byte 80. The installer therefore
also builds `/usr/local/bin/huesync-squeezelite-fifo` from the same pinned revision,
with VISEXPORT but without the v1 patch. Runtime selects it only for `bars_source=cava`.
There is no fallback between these executables; a missing binary is an explicit error.
Both hashes are verified against the installation manifest. This preserves the
intentional external-FIFO feature without weakening canonical v1 requirements.

For static/unit checks on development hosts, use [testing.md](testing.md).
`bash scripts/validate.sh` provides supplementary target diagnostics and a synthetic
canonical-analysis smoke test using the installed environment; it never consumes live PCM.

## Preserve configuration before upgrades

Use the UI's **Backup and restore** section or the installed
`python -m huesync.backup export` command before an upgrade. Portable backups include
controller credentials; choose a private destination. The installer does not export
secrets automatically. Its migration backup remains a separate exact-byte schema
rollback file. See [backup and restore](configuration.md#backup-and-restore).

### Conflicting packaged Squeezelite

Before replacing audio binaries, the installer inspects native systemd and
SysV-generated services for non-HueSync Squeezelite executables. It logs each
conflicting unit, disables it, explicitly stops it, and checks for remaining
processes. An ineffective stop aborts installation rather than starting a
competing ALSA consumer. HueSync's `/usr/local/bin/squeezelite` and
`/usr/local/bin/huesync-squeezelite-fifo` are excluded.

`--check` reports discovered units and their active/inactive state without
stopping or disabling anything. Active conflicts fail the check; an inactive
packaged unit is reported for visibility. Detached unmanaged Squeezelite
processes also fail verification and are never killed speculatively.

AirPlay builds include Shairport's metadata support. The installer provisions
`/run/huesync/airplay.metadata` alongside the audio FIFO (both `0600`, owned by
`huesync`). The service explicitly selects that metadata pipe even during an
upgrade with an existing receiver configuration. Activation writes the managed
metadata settings, including ten-second progress corrections. `--check` verifies
the compiled metadata capability and the metadata FIFO without consuming it.
