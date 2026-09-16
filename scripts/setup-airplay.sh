#!/usr/bin/env bash
# Build and install shairport-sync (AirPlay 2) + nqptp from source.
# Enables AirPlay 2 as an input source for HueSync on Debian 13 (Trixie).
# Safe to re-run: source trees are checked out at exact pinned release commits.
#
# Usage (on the LXC, as root):
#   sudo ./scripts/install-huesync.sh (invokes this build helper)
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
DEFER_START="${HUESYNC_DEFER_START:-0}"
# Upstream make install creates a sample config at the live path. Distinguish
# that first-install sample from an existing operator-owned receiver config.
CONFIG_EXISTED=0
if [[ -f /usr/local/etc/shairport-sync.conf ]]; then CONFIG_EXISTED=1; fi

if [[ $EUID -ne 0 ]]; then
    echo "error: must be run as root" >&2
    exit 1
fi

SRC="${AIRPLAY_BUILD_DIR:-/var/cache/huesync/airplay}"
NQPTP_COMMIT=591f425d9da69f1c4e09f3ad09611b758937b3e5
SHAIRPORT_COMMIT=0b1c4391ffd398e7b145eb4b98416261380adeea

checkout_pinned() {
    local name="$1" revision="$2"
    if [[ ! -d "$SRC/$name/.git" ]]; then
        git clone "https://github.com/mikebrady/$name.git" "$SRC/$name"
    fi
    git -C "$SRC/$name" fetch origin "$revision"
    git -C "$SRC/$name" checkout --detach "$revision"
    [[ "$(git -C "$SRC/$name" rev-parse HEAD)" == "$revision" ]]
    git -C "$SRC/$name" submodule update --init --recursive
}

# ---------------------------------------------------------------------------
# Helper: verify all pkg-config modules and tool binaries needed by
# shairport-sync's configure are present. Prints every missing item with
# the apt package that provides it, then exits non-zero if anything is
# absent. Called after Phase 1 so failures surface before the long build.
# ---------------------------------------------------------------------------
check_build_requirements() {
    local -a missing=()

    # pkg-config module name → apt package that provides the .pc file.
    # Note: "systemd" (.pc from systemd-dev) ≠ "libsystemd" (.pc from
    # libsystemd-dev). shairport-sync configure queries the former for
    # systemdsystemunitdir; libsystemd-dev alone does NOT satisfy this.
    for spec in \
        "systemd:systemd-dev" \
        "libsodium:libsodium-dev" \
        "libplist-2.0:libplist-dev" \
        "avahi-client:libavahi-client-dev" \
        "openssl:libssl-dev" \
        "soxr:libsoxr-dev" \
        "libavutil:libavutil-dev" \
        "libavcodec:libavcodec-dev" \
        "libavformat:libavformat-dev" \
        "libswresample:libswresample-dev" \
        "libgcrypt:libgcrypt20-dev"; do
        local mod="${spec%%:*}" deb="${spec##*:}"
        if ! pkg-config --exists "$mod" 2>/dev/null; then
            missing+=("  [MISSING] pkg-config ${mod}  →  apt install ${deb}")
        fi
    done

    for spec in "plistutil:libplist-utils" "xxd:xxd"; do
        local tool="${spec%%:*}" deb="${spec##*:}"
        if ! command -v "$tool" &>/dev/null; then
            missing+=("  [MISSING] tool ${tool}  →  apt install ${deb}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        echo "error: missing build requirements — fix Phase 1 and retry:" >&2
        for item in "${missing[@]}"; do
            echo "$item" >&2
        done
        exit 1
    fi

    echo "  All build requirements satisfied."
}

# ---------------------------------------------------------------------------
# Helper: make a systemd unit visible after make SHELL=/bin/bash install.
# make SHELL=/bin/bash install may write the service file to /usr/local/lib/systemd/system/
# (default --prefix=/usr/local path), which is outside systemd's search
# path. Symlinks it into /etc/systemd/system/ so daemon-reload picks it up.
# ---------------------------------------------------------------------------
ensure_unit_visible() {
    local unit_name="$1"
    local service_file="${unit_name}.service"

    if systemctl cat "$service_file" &>/dev/null; then
        return 0  # already visible
    fi

    local src
    src=""
    local directory
    for directory in /usr/local/lib/systemd/system /usr/lib/systemd/system /lib/systemd/system; do
        if [[ -f "$directory/$service_file" ]]; then
            src="$directory/$service_file"
            break
        fi
    done

    if [[ -z "$src" ]]; then
        echo "error: $service_file not found after make SHELL=/bin/bash install" >&2
        return 1
    fi

    echo "  linking $src → /etc/systemd/system/$service_file"
    ln -sf "$src" "/etc/systemd/system/$service_file"
}

# ---------------------------------------------------------------------------
# Phase 1: build dependencies + verification
# Two distinct systemd packages:
#   systemd-dev     → systemd.pc   (queried by shairport-sync for unit dir)
#   libsystemd-dev  → libsystemd.pc (for programs linking against libsystemd)
# Both are required; libsystemd-dev alone was the source of the
# "pkg-config unable to query systemd" configure error.
# ---------------------------------------------------------------------------
echo "==> [1/6] Checking installer-provisioned dependencies..."
if [[ "${HUESYNC_DEPENDENCIES_READY:-0}" != 1 ]]; then
    echo "error: use scripts/install-huesync.sh to provision dependencies" >&2
    exit 1
fi

echo "==> [1/6] Verifying build requirements..."
check_build_requirements

# ---------------------------------------------------------------------------
# Phase 2: nqptp (precision timing — must be running before shairport-sync)
# nqptp binds UDP 319 and 320 (PTP ports) and runs as root. Within the
# LXC network namespace, root can bind privileged ports even in an
# unprivileged container.
# ---------------------------------------------------------------------------
echo "==> [2/6] Building nqptp..."
mkdir -p "$SRC"
checkout_pinned nqptp "$NQPTP_COMMIT"

cd "$SRC/nqptp"
autoreconf -fi
./configure --with-systemd-startup
make -j"$(nproc)"
make SHELL=/bin/bash install
ensure_unit_visible nqptp
mkdir -p /etc/systemd/system/nqptp.service.d
cat > /etc/systemd/system/nqptp.service.d/huesync-binary.conf << 'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/nqptp
EOF

# ---------------------------------------------------------------------------
# Phase 3: shairport-sync with AirPlay 2 + pipe output
# --with-airplay-2 automatically enables FFmpeg (configure.ac: "if
#   with_airplay_2=yes; using_ffmpeg=true") — no --with-avcodec needed.
# --with-apple-alac is explicitly forbidden with AP2 (configure errors).
# --with-systemd-startup (not --with-systemd) installs the service file.
# ---------------------------------------------------------------------------
echo "==> [3/6] Building shairport-sync (AirPlay 2)..."
checkout_pinned shairport-sync "$SHAIRPORT_COMMIT"

cd "$SRC/shairport-sync"
autoreconf -fi
./configure \
    --with-airplay-2 \
    --with-avahi \
    --with-ssl=openssl \
    --with-soxr \
    --with-pipe \
    --with-systemd-startup

make -j"$(nproc)"
make SHELL=/bin/bash install

# ---------------------------------------------------------------------------
# Phase 4: runtime directory and AirPlay FIFO
# /run/ is tmpfs and is cleared on every reboot. This tmpfiles.d entry
# recreates /run/huesync and pre-creates the AirPlay FIFO at every boot.
#
# Both entries are needed:
#   d  — directory, so shairport-sync can write the FIFO into it
#   p  — named pipe (FIFO), pre-created so HueSync can open it before
#         an iOS client connects.  shairport-sync opens the existing FIFO
#         for writing when a session starts; no mkfifo from shairport-sync
#         is required.
#
# Without the 'p' entry the FIFO is created only when iOS connects, which
# means HueSync's pipe_source.open() (O_RDONLY|O_NONBLOCK) raises OSError
# if a coupling is activated before the first iOS AirPlay session.
#
# Ownership: both huesync user — shairport-sync runs as huesync (drop-in),
# HueSync runs as huesync.  Mode 0600 restricts access to that user only.
# HueSync's own cava FIFOs use /tmp/huesync (player_manager._RUN_DIR);
# /run/huesync is solely for the AirPlay pipe.
# ---------------------------------------------------------------------------
echo "==> [4/6] Configuring runtime directory and AirPlay FIFO..."
printf 'd /run/huesync             0755 huesync huesync -\np /run/huesync/airplay.pcm 0600 huesync huesync -\n' \
    > /etc/tmpfiles.d/huesync-run.conf
systemd-tmpfiles --create /etc/tmpfiles.d/huesync-run.conf

# ---------------------------------------------------------------------------
# Phase 5: shairport-sync configuration
# No audio output from the LXC — pipe backend only. HueSync reads the raw
# PCM stream (S16_LE stereo 44100 Hz) from the pipe for spectrum analysis.
# ---------------------------------------------------------------------------
echo "==> [5/6] Writing shairport-sync config..."
if [[ "$CONFIG_EXISTED" == 0 ]]; then
cat > /usr/local/etc/shairport-sync.conf << 'EOF'
general = {
  name = "HueSync";
  output_backend = "pipe";
}

pipe = {
  name = "/run/huesync/airplay.pcm";
  output_rate = 44100;
  output_format = "S16_LE";
  output_channels = 2;
}
EOF
fi
# Allow the huesync service to overwrite the name field at activation time.
chown huesync:huesync /usr/local/etc/shairport-sync.conf

# ---------------------------------------------------------------------------
# Phase 6: systemd service — run as huesync user
# make SHELL=/bin/bash install creates a shairport-sync system user. This drop-in overrides
# that so the service runs as huesync instead, giving it write access to
# /run/huesync/ without extra group membership.
# ---------------------------------------------------------------------------
echo "==> [6/6] Installing systemd drop-in and starting services..."
ensure_unit_visible shairport-sync
mkdir -p /etc/systemd/system/shairport-sync.service.d
cat > /etc/systemd/system/shairport-sync.service.d/run-as-huesync.conf << 'EOF'
[Service]
User=huesync
Group=huesync
ExecStart=
ExecStart=/usr/local/bin/shairport-sync --configfile=/usr/local/etc/shairport-sync.conf
EOF

# Standard installer starts services only after artifact/schema verification.
/usr/local/bin/shairport-sync --version
[[ -p /run/huesync/airplay.pcm ]]
if [[ "$DEFER_START" != 1 ]]; then
    systemctl daemon-reload
    systemctl enable --now avahi-daemon nqptp shairport-sync
fi
