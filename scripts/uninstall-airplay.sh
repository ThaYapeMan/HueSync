#!/usr/bin/env bash
# Remove all shairport-sync and nqptp artefacts installed by setup-airplay.sh.
# Run this before re-running setup-airplay.sh to guarantee a clean slate.
#
# Usage (on the LXC, as root):
#   sudo bash scripts/uninstall-airplay.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "error: must be run as root" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: stop and disable services
# ---------------------------------------------------------------------------
echo "==> [1/6] Stopping and disabling services..."
systemctl stop    shairport-sync 2>/dev/null || true
systemctl disable shairport-sync 2>/dev/null || true
systemctl stop    nqptp          2>/dev/null || true
systemctl disable nqptp          2>/dev/null || true

# ---------------------------------------------------------------------------
# Step 2: remove systemd unit files and drop-in overrides
# make install may write to /usr/local/lib/systemd/system/ (non-standard);
# setup-airplay.sh symlinks those into /etc/systemd/system/ so systemd
# finds them. Remove all three locations for each unit.
# ---------------------------------------------------------------------------
echo "==> [2/6] Removing systemd unit files..."
rm -f  /etc/systemd/system/shairport-sync.service
rm -f  /lib/systemd/system/shairport-sync.service
rm -f  /usr/local/lib/systemd/system/shairport-sync.service
rm -f  /etc/systemd/system/nqptp.service
rm -f  /lib/systemd/system/nqptp.service
rm -f  /usr/local/lib/systemd/system/nqptp.service
rm -rf /etc/systemd/system/shairport-sync.service.d

# ---------------------------------------------------------------------------
# Step 3: remove installed binaries
# Default autoconf prefix is /usr/local; binaries land in bin/ or sbin/.
# ---------------------------------------------------------------------------
echo "==> [3/6] Removing installed binaries..."
rm -f /usr/local/bin/shairport-sync
rm -f /usr/local/sbin/shairport-sync
rm -f /usr/local/sbin/nqptp

# ---------------------------------------------------------------------------
# Step 4: remove source trees (full rm -rf; next run does a fresh clone)
# ---------------------------------------------------------------------------
echo "==> [4/6] Removing source directories..."
rm -rf /usr/local/src/nqptp
rm -rf /usr/local/src/shairport-sync

# ---------------------------------------------------------------------------
# Step 5: remove config files
# ---------------------------------------------------------------------------
echo "==> [5/6] Removing config files..."
rm -f /usr/local/etc/shairport-sync.conf
rm -f /etc/tmpfiles.d/huesync-run.conf

# ---------------------------------------------------------------------------
# Step 6: remove runtime directory
# /run/huesync is created solely by setup-airplay.sh (via tmpfiles.d).
# HueSync's own FIFOs live in /tmp/huesync (player_manager._RUN_DIR),
# so removing /run/huesync does not affect a running HueSync instance.
# ---------------------------------------------------------------------------
echo "==> [6/6] Removing runtime directory and reloading systemd..."
rm -rf /run/huesync
systemctl daemon-reload

echo ""
echo "Uninstall complete. Run 'sudo bash scripts/setup-airplay.sh' for a fresh install."
