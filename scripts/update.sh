#!/usr/bin/env bash
# Standard HueSync deployment update.
#
# Pulls the latest committed code, synchronizes Python dependencies with the
# current pyproject.toml, and restarts the HueSync service.
# Safe to re-run: git pull is fast-forward only; pip install is idempotent.
#
# Usage (on the LXC / target machine, as root):
#   cd /opt/huesync
#   bash scripts/update.sh
#
# After completion, run:
#   bash scripts/validate.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "error: must be run as root" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"

if [[ ! -x "$VENV/bin/pip" ]]; then
    echo "error: virtualenv not found at $VENV" >&2
    echo "       Create it first: python3 -m venv $VENV && $VENV/bin/pip install -e $REPO_DIR" >&2
    exit 1
fi

echo "==> HueSync update: $REPO_DIR"

# ---------------------------------------------------------------------------
# Step 1: pull latest committed code
# ---------------------------------------------------------------------------
echo ""
echo "[1/3] Pulling latest commits..."
git -C "$REPO_DIR" pull --ff-only
echo "  HEAD: $(git -C "$REPO_DIR" rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
# Step 2: synchronize Python dependencies
# Always runs because pyproject.toml may declare new deps since the last pull.
# pip install in editable mode also re-runs hatch_build.py, which embeds the
# current git commit hash into _commit.py so /api/status reports the right build.
# ---------------------------------------------------------------------------
echo ""
echo "[2/3] Synchronizing Python dependencies..."
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"
echo "  Done."

# ---------------------------------------------------------------------------
# Step 3: restart HueSync
# ---------------------------------------------------------------------------
echo ""
echo "[3/3] Restarting huesync service..."
systemctl restart huesync
sleep 2
STATUS=$(systemctl is-active huesync 2>/dev/null || echo "failed")
echo "  huesync: $STATUS"
if [[ "$STATUS" != "active" ]]; then
    echo ""
    echo "error: huesync did not start cleanly. Check logs:" >&2
    echo "  journalctl -u huesync -n 40" >&2
    exit 1
fi

echo ""
echo "Update complete. Run 'bash scripts/validate.sh' to verify."
