#!/usr/bin/env bash
# Repository-owned Debian 13 deployment. Build in isolation; never pip -e the checkout.
set -Eeuo pipefail
export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFIX=/opt/huesync
CONFIG=/etc/huesync/config.json
# Build tools/headers; -dev packages pull the matching runtime shared libraries.
BUILD_PACKAGES=(git ca-certificates build-essential pkg-config patch python3-dev python3-venv
    curl xz-utils libfftw3-dev libasound2-dev libflac-dev libmad0-dev libmpg123-dev
    libvorbis-dev libfaad-dev libssl-dev autoconf automake libtool libpopt-dev
    libconfig-dev systemd-dev libsystemd-dev libavahi-client-dev libavahi-common-dev
    libsoxr-dev libsodium-dev libgcrypt20-dev libplist-dev libplist-utils uuid-dev
    libavutil-dev libavcodec-dev libavformat-dev libswresample-dev)
RUNTIME_PACKAGES=(python3 systemd util-linux libcap2-bin polkitd avahi-daemon alsa-utils cava xxd)
# Squeezelite default codecs: PCM, FLAC, Vorbis, MAD/MPG123 MP3, FAAD AAC.
# No OPUS/FFMPEG/ALAC/RESAMPLE flags are enabled by this standard build.
log() { printf '\n==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
platform() {
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ "$ID" == debian && "$VERSION_ID" == 13 && "${VERSION_CODENAME:-}" == trixie ]] ||
        fail 'Supported target: Debian 13 (trixie) only'
    [[ "$(uname -m)" == x86_64 ]] || fail 'Supported target: x86_64 only'
    [[ -d /run/systemd/system ]] || fail 'A booted systemd target is required (not a build-only chroot)'
}
repo_check() {
    COMMIT=$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse HEAD)
    SHORT=$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" rev-parse --short HEAD)
    [[ -z "$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]] ||
        fail 'Tracked checkout changes exist; commit or use a clean checkout before deployment'
}
verify() {
    local environment="$1" package
    for package in "${BUILD_PACKAGES[@]}" "${RUNTIME_PACKAGES[@]}"; do
        [[ "$(dpkg-query -W -f='${Status}' "$package")" == 'install ok installed' ]] ||
            fail "Missing package: $package"
    done
    pkg-config --exists alsa fftw3
    local binary linkage
    for binary in /usr/local/bin/squeezelite /usr/local/bin/huesync-squeezelite-fifo /usr/local/bin/shairport-sync /usr/local/bin/nqptp; do
        linkage=$(ldd "$binary")
        [[ "$linkage" != *"not found"* ]] || fail "Unresolved runtime libraries: $binary"
    done
    printf 'ALSA: PASS\nFFTW: PASS\n'
    [[ -x /usr/local/bin/squeezelite ]] || fail 'HueSync Squeezelite missing'
    [[ "$(command -v squeezelite)" == /usr/local/bin/squeezelite ]] || fail 'Wrong Squeezelite PATH'
    nm /usr/local/bin/squeezelite | grep 'vis_shm_v1_finish_init$' >/dev/null || fail 'SHM v1 producer missing'
    [[ -x /usr/local/bin/shairport-sync && -x /usr/local/bin/nqptp ||
       -x /usr/local/bin/shairport-sync && -x /usr/local/sbin/nqptp ]] || fail 'AirPlay binaries missing'
    /usr/local/bin/shairport-sync --version | grep -i 'AirPlay2' >/dev/null || fail 'AirPlay 2 not built'
    [[ -p /run/huesync/airplay.pcm ]] || fail 'AirPlay FIFO missing'
    [[ -f /etc/polkit-1/rules.d/49-huesync-airplay.rules ]] || fail 'AirPlay service permission missing'
    "$environment/bin/python" -I -B "$SCRIPT_DIR/verify-install.py" "$COMMIT" "$SHORT" "$CONFIG"
    "$environment/bin/python" -I -B -m pip check
    repo_check
    printf 'AirPlay prerequisites: PASS\nRepository tracked state: CLEAN\n'
}
verify_services() {
    local unit
    # is-active with multiple units succeeds if any is active; require every one.
    for unit in huesync shairport-sync nqptp avahi-daemon; do
        systemctl is-active --quiet "$unit" || fail "Service is not active: $unit"
    done
}
case "${1:-}" in
    --help|-h) echo 'Usage: sudo ./scripts/install-huesync.sh | ./scripts/install-huesync.sh --check'; exit 0 ;;
    --check) [[ $# == 1 ]] || fail 'Unexpected arguments'; CHECK=1 ;;
    '') CHECK=0 ;;
    *) fail 'Unknown argument; use --help' ;;
esac
trap 'printf "ERROR: installation/check failed at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
platform
repo_check
if [[ "$CHECK" == 1 ]]; then
    verify "$PREFIX/.venv"
    systemd-analyze verify /etc/systemd/system/huesync.service
    verify_services
    log "CHECK COMPLETE: $COMMIT"
    exit 0
fi
[[ "$EUID" == 0 ]] || fail 'Run installation with sudo (check mode needs no root)'
# No concurrent installs/migrations; check mode acquires no file lock and writes nothing.
exec 9>/run/lock/huesync-install.lock
flock -n 9 || fail 'Another installer is running'
log '1/7 Install system build and runtime dependencies'
apt-get update
apt-get install -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold --no-install-recommends "${BUILD_PACKAGES[@]}" "${RUNTIME_PACKAGES[@]}"
pkg-config --exists alsa fftw3
getent group huesync >/dev/null || groupadd --system huesync
id huesync >/dev/null 2>&1 || useradd --system --gid huesync --home-dir "$PREFIX" --shell /usr/sbin/nologin huesync
usermod -a -G audio huesync
install -d -m 0755 "$PREFIX/releases"
install -d -m 0750 -o huesync -g huesync /etc/huesync
# An earlier checkout-local venv is archived only after the new candidate verifies.
[[ ! -e "$PREFIX/.venv" || -L "$PREFIX/.venv" || -f "$PREFIX/.venv/pyvenv.cfg" ]] ||
    fail '/opt/huesync/.venv exists but is not a recognizable virtual environment'
log '2/7 Isolate committed sources and build the frontend/wheel'
WORK=$(mktemp -d /var/tmp/huesync-install.XXXXXX)
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT
git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" archive HEAD | tar -x -C "$WORK"
curl --fail --location --retry 3 https://nodejs.org/dist/v22.22.0/node-v22.22.0-linux-x64.tar.xz -o "$WORK/node.tar.xz"
printf '%s  %s\n' 9aa8e9d2298ab68c600bd6fb86a6c13bce11a4eca1ba9b39d79fa021755d7c37 "$WORK/node.tar.xz" | sha256sum --check
mkdir "$WORK/node"
tar -xJf "$WORK/node.tar.xz" --strip-components=1 -C "$WORK/node"
(
    cd "$WORK/web"
    PATH="$WORK/node/bin:$PATH" npm ci --no-audit --no-fund
    PATH="$WORK/node/bin:$PATH" npm run build
)
python3 -m venv "$WORK/build-env"
"$WORK/build-env/bin/pip" install build
HUESYNC_BUILD_COMMIT="$SHORT" "$WORK/build-env/bin/python" -m build --wheel --outdir "$WORK/wheels" "$WORK"
RELEASE=$(mktemp -d "$PREFIX/releases/$COMMIT.XXXXXX")
chmod 0755 "$RELEASE"
python3 -m venv "$RELEASE/venv"
"$RELEASE/venv/bin/pip" install "$WORK"/wheels/*.whl
log '3/7 Build pinned SHM v1 Squeezelite and AirPlay 2'
# Do not inherit optional flags or upstream overrides from a shell environment.
env -u OPTS -u SQUEEZELITE_COMMIT -u SQUEEZELITE_REPO BUILD_DIR="$WORK/squeezelite-build" \
    INSTALL_DIR="$WORK/bin" bash "$WORK/scripts/build-squeezelite.sh"
# Stop owned audio services before replacing binaries or persisted data.
for unit in huesync shairport-sync nqptp; do
    if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi
done
install -m 0755 "$WORK/bin/squeezelite" /usr/local/bin/squeezelite
cmp "$WORK/bin/squeezelite" /usr/local/bin/squeezelite
install -m 0755 "$WORK/bin/huesync-squeezelite-fifo" /usr/local/bin/huesync-squeezelite-fifo
cmp "$WORK/bin/huesync-squeezelite-fifo" /usr/local/bin/huesync-squeezelite-fifo
AIRPLAY_BUILD_DIR="$WORK/airplay" HUESYNC_DEFER_START=1 HUESYNC_DEPENDENCIES_READY=1 bash "$WORK/scripts/setup-airplay.sh"
log '4/7 Migrate persisted configuration before starting current runtime'
"$RELEASE/venv/bin/python" -I -B -m huesync.migration "$CONFIG"
chown huesync:huesync "$CONFIG"
chmod 0640 "$CONFIG"
log '5/7 Install the repository service and narrow AirPlay restart authorization'
install -m 0644 "$WORK/systemd/huesync.service" /etc/systemd/system/huesync.service
install -d /etc/polkit-1/rules.d
install -m 0644 "$WORK/systemd/49-huesync-airplay.rules" /etc/polkit-1/rules.d/
systemctl daemon-reload
"$RELEASE/venv/bin/python" -I -B "$WORK/scripts/write-install-manifest.py" "$COMMIT" "$SHORT" "$RELEASE"
log '6/7 Verify installed artifacts and current schema before activation'
verify "$RELEASE/venv"
# Stable executable path from the existing unit. Venv itself is never relocated.
if [[ -d "$PREFIX/.venv" && ! -L "$PREFIX/.venv" ]]; then
    mv "$PREFIX/.venv" "$RELEASE/previous-venv-backup"
fi
ln -sfn "$RELEASE/venv" "$PREFIX/.venv.next"
mv -Tf "$PREFIX/.venv.next" "$PREFIX/.venv"
systemd-analyze verify /etc/systemd/system/huesync.service
log '7/7 Start verified services'
systemctl enable avahi-daemon nqptp shairport-sync huesync
systemctl restart avahi-daemon nqptp shairport-sync huesync
verify_services
curl --fail --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:8420/api/status >/dev/null
repo_check
printf '\nINSTALLATION COMPLETE\nGit commit: %s\nPython: %s\nSqueezelite: /usr/local/bin/squeezelite\n' "$COMMIT" "$RELEASE/venv"
sha256sum /usr/local/bin/squeezelite
printf 'Logs: journalctl -u huesync -u shairport-sync -u nqptp\nUI: http://<target>:8420\n'
printf 'LMS pacing requires host-provided /dev/snd devices; LXC host configuration is not modified.\n'
