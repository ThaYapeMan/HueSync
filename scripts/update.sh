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
    echo "  Virtualenv not found; creating $VENV ..."
    python3 -m venv "$VENV"
    echo "  Done."
fi

echo "==> HueSync update: $REPO_DIR"

# ---------------------------------------------------------------------------
# Step 1: pull latest committed code
# ---------------------------------------------------------------------------
echo ""
echo "[1/4] Pulling latest commits..."
git -C "$REPO_DIR" pull --ff-only
echo "  HEAD: $(git -C "$REPO_DIR" rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
# Step 2: install / verify native build dependencies
# hatch_build.py compiles _libcavacore.so during pip install and requires:
#   build-essential — gcc + standard C headers
#   libfftw3-dev    — FFTW3 development headers + runtime shared library
# libfftw3-dev pulls in the runtime FFTW3 shared library as a transitive
# dependency, so we do not have to list the runtime package separately.
# dpkg check avoids a network round-trip when packages are already present.
# apt-get install is idempotent: already-installed packages are a no-op.
# ---------------------------------------------------------------------------
echo ""
echo "[2/4] Verifying native build dependencies..."
_need_apt=0
# The runtime FFTW3 shared library is a transitive dependency of libfftw3-dev;
# installing only these two packages is sufficient.
for _pkg in build-essential libfftw3-dev; do
    if ! dpkg -l "$_pkg" 2>/dev/null | grep -q "^ii"; then
        echo "  [missing] $_pkg"
        _need_apt=1
    fi
done

if [[ $_need_apt -eq 1 ]]; then
    echo "  Installing missing packages..."
    apt-get update -qq
    apt-get install -y build-essential libfftw3-dev
    echo "  Done."
else
    echo "  All required packages already installed."
fi

# ---------------------------------------------------------------------------
# Step 3: synchronize Python dependencies
# Always runs because pyproject.toml may declare new deps since the last pull.
# pip install in editable mode also re-runs hatch_build.py, which:
#   - embeds the current git commit hash into _commit.py
#   - compiles src/huesync/cavacore/_libcavacore.so (requires step 2 above)
# ---------------------------------------------------------------------------
echo ""
echo "[3/4] Synchronizing Python dependencies..."
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"
echo "  Done."

# ---------------------------------------------------------------------------
# Step 4: restart HueSync
# ---------------------------------------------------------------------------
echo ""
echo "[4/4] Restarting huesync service..."
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
