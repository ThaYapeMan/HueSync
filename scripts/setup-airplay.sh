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
# Phase 1: build dependencies
# ---------------------------------------------------------------------------
echo "==> [1/6] Installing build dependencies..."
apt-get update -qq
apt-get install -y \
    build-essential git autoconf automake libtool pkg-config \
    libpopt-dev libconfig-dev libssl-dev libsystemd-dev \
    libavahi-client-dev libavahi-common-dev avahi-daemon \
    libsoxr-dev libsodium-dev libgcrypt20-dev \
    libplist-dev libplist-utils \
    uuid-dev xxd \
    libavutil-dev libavcodec-dev libavformat-dev libswresample-dev

systemctl enable avahi-daemon
systemctl start avahi-daemon

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

systemctl daemon-reload
systemctl enable nqptp
systemctl restart nqptp

# ---------------------------------------------------------------------------
# Phase 3: shairport-sync with AirPlay 2 + pipe output
# --with-apple-alac: Apple's bundled ALAC submodule for lossless streams.
# --with-avcodec: FFmpeg for AAC-ELD decoding — AirPlay 2 sends AAC-ELD on
#   low-latency streams; apple-alac alone does not cover this codec.
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
    --with-apple-alac \
    --with-avcodec \
    --with-systemd

# Verify AirPlay 2 and pipe are enabled before the lengthy compile step.
if ! grep -q "AirPlay 2.*yes" config.log 2>/dev/null; then
    echo ""
    echo "  Tip: check ./config.log for 'AirPlay 2' and 'pipe' status if build fails."
fi

make -j"$(nproc)"
make install

# ---------------------------------------------------------------------------
# Phase 4: runtime directory
# /run/ is tmpfs and is cleared on every reboot. This tmpfiles.d entry
# recreates /run/huesync at boot so the pipe path survives restarts.
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
