#!/usr/bin/env bash
# Build and install shairport-sync (AirPlay 2) + nqptp from source.
# Enables AirPlay 2 as an input source for HueSync on Debian 13 (Trixie).
# Safe to re-run: existing source trees are updated via git pull, not re-cloned.
#
# Usage (on the LXC, as root):
#   sudo bash scripts/setup-airplay.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "error: must be run as root" >&2
    exit 1
fi

SRC=/usr/local/src

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
# Helper: make a systemd unit visible after make install.
# make install may write the service file to /usr/local/lib/systemd/system/
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
    src=$(find /usr/local/lib/systemd/system /usr/lib/systemd/system \
               -name "$service_file" 2>/dev/null | head -1)

    if [[ -z "$src" ]]; then
        echo "error: $service_file not found after make install" >&2
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
echo "==> [1/6] Installing build dependencies..."
apt-get update -qq
apt-get install -y \
    build-essential git autoconf automake libtool pkg-config \
    libpopt-dev libconfig-dev libssl-dev \
    systemd-dev libsystemd-dev \
    libavahi-client-dev libavahi-common-dev avahi-daemon \
    libsoxr-dev libsodium-dev libgcrypt20-dev \
    libplist-dev libplist-utils \
    uuid-dev xxd \
    libavutil-dev libavcodec-dev libavformat-dev libswresample-dev

systemctl enable avahi-daemon
systemctl start avahi-daemon

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
if [[ -d "$SRC/nqptp/.git" ]]; then
    git -C "$SRC/nqptp" pull --ff-only
else
    git clone https://github.com/mikebrady/nqptp.git "$SRC/nqptp"
fi

cd "$SRC/nqptp"
autoreconf -fi
./configure --with-systemd-startup
make -j"$(nproc)"
make install
ensure_unit_visible nqptp

systemctl daemon-reload
systemctl enable nqptp
systemctl restart nqptp

# ---------------------------------------------------------------------------
# Phase 3: shairport-sync with AirPlay 2 + pipe output
# --with-airplay-2 automatically enables FFmpeg (configure.ac: "if
#   with_airplay_2=yes; using_ffmpeg=true") — no --with-avcodec needed.
# --with-apple-alac is explicitly forbidden with AP2 (configure errors).
# --with-systemd-startup (not --with-systemd) installs the service file.
# ---------------------------------------------------------------------------
echo "==> [3/6] Building shairport-sync (AirPlay 2)..."
if [[ -d "$SRC/shairport-sync/.git" ]]; then
    git -C "$SRC/shairport-sync" pull --ff-only
    git -C "$SRC/shairport-sync" submodule update --init --recursive
else
    git clone --recurse-submodules \
        https://github.com/mikebrady/shairport-sync.git \
        "$SRC/shairport-sync"
fi

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
make install

# ---------------------------------------------------------------------------
# Phase 4: runtime directory
# /run/ is tmpfs and is cleared on every reboot. This tmpfiles.d entry
# recreates /run/huesync at boot so the pipe path survives restarts.
# HueSync's own FIFOs use /tmp/huesync (player_manager._RUN_DIR) — this
# directory is solely for the AirPlay pipe.
# ---------------------------------------------------------------------------
echo "==> [4/6] Configuring runtime directory..."
echo 'd /run/huesync 0755 huesync huesync -' \
    > /etc/tmpfiles.d/huesync-run.conf
systemd-tmpfiles --create /etc/tmpfiles.d/huesync-run.conf

# ---------------------------------------------------------------------------
# Phase 5: shairport-sync configuration
# No audio output from the LXC — pipe backend only. HueSync reads the raw
# PCM stream (S16_LE stereo 44100 Hz) from the pipe for spectrum analysis.
# ---------------------------------------------------------------------------
echo "==> [5/6] Writing shairport-sync config..."
cat > /usr/local/etc/shairport-sync.conf << 'EOF'
general = {
  name = "HueSync";
  output_backend = "pipe";
}

pipe = {
  name = "/run/huesync/airplay.pcm";
}
EOF

# ---------------------------------------------------------------------------
# Phase 6: systemd service — run as huesync user
# make install creates a shairport-sync system user. This drop-in overrides
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
EOF

systemctl daemon-reload
systemctl enable shairport-sync
systemctl restart shairport-sync

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
echo ""
echo "==> Verification"

NQPTP_STATUS=$(systemctl is-active nqptp 2>/dev/null || echo "failed")
SPS_STATUS=$(systemctl is-active shairport-sync 2>/dev/null || echo "failed")
SPS_VERSION=$(shairport-sync --version 2>&1 | head -1 || echo "unknown")
PIPE=/run/huesync/airplay.pcm

echo "  nqptp:            $NQPTP_STATUS"
echo "  shairport-sync:   $SPS_STATUS"
echo "  version:          $SPS_VERSION"

if [[ -p "$PIPE" ]]; then
    echo "  pipe:             $PIPE  [FIFO — ready]"
else
    echo "  pipe:             $PIPE  [not yet created — appears when iOS connects]"
fi

if [[ "$NQPTP_STATUS" != "active" || "$SPS_STATUS" != "active" ]]; then
    echo ""
    echo "One or more services are not active. Check logs:"
    echo "  journalctl -u nqptp -n 30"
    echo "  journalctl -u shairport-sync -n 30"
    exit 1
fi

echo ""
echo "Setup complete."
echo "Open iOS Control Center → AirPlay and verify 'HueSync' appears alongside"
echo "your Sonos speakers. Select both simultaneously to confirm AirPlay 2"
echo "multi-room co-existence before enabling AirPlay input in HueSync."
