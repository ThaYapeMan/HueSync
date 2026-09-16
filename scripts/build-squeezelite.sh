#!/usr/bin/env bash
# Build a squeezelite binary with the HueSync v1 visualiser SHM producer.
#
# This script automates the previously manual "copy files and patch
# output_vis.c" ritual described in squeezelite/README.md.
#
#   1. Clone ralph-irving/squeezelite at a pinned tag.
#   2. Copy squeezelite/vis_shm_v1.h and squeezelite/output_vis_v1.c into the
#      source tree.
#   3. Apply squeezelite/output_vis_v1.patch to hook the v1 producer into
#      output_vis.c and the build.
#   4. Compile.
#   5. Install to $INSTALL_DIR (default: /usr/local/bin).
#
# The script is idempotent: re-running it wipes the previous checkout and
# starts from scratch, so a partially-applied patch cannot poison the next
# build.
#
# Usage:
#   sudo bash scripts/build-squeezelite.sh
#   sudo INSTALL_DIR=/opt/huesync/bin bash scripts/build-squeezelite.sh
#
# Runtime requirements (Debian 13, matches the LXC deployment target):
#   build-essential   — gcc + make + libc headers
#   libasound2-dev    — ALSA output backend
#   libflac-dev libmad0-dev libmpg123-dev libvorbis-dev libfaad-dev libopus-dev
#                     — codec decoders squeezelite links against
#   libssl-dev        — TLS for https:// stream sources
#   git               — clone the upstream repository
#
# The build script does not install these itself; it expects the caller to
# have run the standard HueSync installer first.  A missing dependency
# surfaces as a `make` failure with an actionable message.

set -euo pipefail

# Pinned upstream revision.  Upstream (ralph-irving/squeezelite) does not
# publish git tags, so we pin the exact commit hash.  Update deliberately,
# in a dedicated commit, alongside any patch adjustments required.
SQUEEZELITE_COMMIT="${SQUEEZELITE_COMMIT:-c7c4248ddd70e47dbfeba0bf4a8a7ec08d8a995c}"
SQUEEZELITE_REPO="${SQUEEZELITE_REPO:-https://github.com/ralph-irving/squeezelite.git}"

INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

# Directory containing this script; the sibling squeezelite/ directory holds
# the patch and the two v1 producer sources.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$REPO_DIR/squeezelite"

BUILD_DIR="${BUILD_DIR:-/tmp/huesync-squeezelite-build}"

echo "==> HueSync squeezelite producer build"
echo "    upstream commit: $SQUEEZELITE_COMMIT"
echo "    build dir:       $BUILD_DIR"
echo "    install to:      $INSTALL_DIR/squeezelite"
echo ""

for _tool in git make gcc patch; do
    if ! command -v "$_tool" >/dev/null 2>&1; then
        echo "error: required tool '$_tool' not found in PATH" >&2
        echo "       install build-essential + git before running this script" >&2
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# [1] Fresh checkout at the pinned commit
# ---------------------------------------------------------------------------
echo "[1/5] Cloning $SQUEEZELITE_REPO and checking out $SQUEEZELITE_COMMIT..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
# Upstream publishes no tags, so we clone the default branch and pin the
# exact commit hash.  Depth 200 is enough history for the checkout to
# succeed on the current master; if the commit falls out of that window
# a subsequent `git fetch --unshallow` will make it reachable.
git clone --quiet --depth 200 "$SQUEEZELITE_REPO" "$BUILD_DIR/squeezelite"
(
    cd "$BUILD_DIR/squeezelite"
    if ! git checkout --quiet "$SQUEEZELITE_COMMIT" 2>/dev/null; then
        git fetch --quiet --unshallow origin || true
        git checkout --quiet "$SQUEEZELITE_COMMIT"
    fi
    actual_hash="$(git rev-parse HEAD)"
    if [[ "$actual_hash" != "$SQUEEZELITE_COMMIT" ]]; then
        echo "error: checked-out revision $actual_hash does not match pinned commit $SQUEEZELITE_COMMIT" >&2
        exit 1
    fi
    echo "    pinned revision verified: $actual_hash"
)

# ---------------------------------------------------------------------------
# [2] Drop the producer sources into the tree
# ---------------------------------------------------------------------------
echo "[2/5] Installing v1 producer sources..."
cp "$PATCH_DIR/vis_shm_v1.h"     "$BUILD_DIR/squeezelite/vis_shm_v1.h"
cp "$PATCH_DIR/output_vis_v1.c"  "$BUILD_DIR/squeezelite/output_vis_v1.c"

# ---------------------------------------------------------------------------
# [3] Apply the patch that hooks the v1 producer into output_vis.c + Makefile
# ---------------------------------------------------------------------------
echo "[3/5] Applying HueSync v1 producer patch..."
if [[ ! -f "$PATCH_DIR/output_vis_v1.patch" ]]; then
    echo "error: patch not found: $PATCH_DIR/output_vis_v1.patch" >&2
    exit 1
fi
# Dry-run first so any rejection surfaces before we begin mutating files.
(
    cd "$BUILD_DIR/squeezelite"
    if ! patch --dry-run -p1 --forward < "$PATCH_DIR/output_vis_v1.patch"; then
        echo "error: HueSync producer patch does not apply cleanly to $SQUEEZELITE_COMMIT" >&2
        echo "       regenerate output_vis_v1.patch against this upstream revision" >&2
        exit 1
    fi
    patch -p1 --forward < "$PATCH_DIR/output_vis_v1.patch"
)

# ---------------------------------------------------------------------------
# [4] Build
# ---------------------------------------------------------------------------
echo "[4/5] Building squeezelite..."
(
    cd "$BUILD_DIR/squeezelite"
    # OPTS lets the caller trim features (e.g. -DNO_FAAD).  Default matches
    # the upstream release build.
    make -j"$(nproc)" ${OPTS:+"OPTS=$OPTS"}
)

# ---------------------------------------------------------------------------
# [5] Install
# ---------------------------------------------------------------------------
echo "[5/5] Installing to $INSTALL_DIR/squeezelite..."
install -d "$INSTALL_DIR"
install -m 0755 "$BUILD_DIR/squeezelite/squeezelite" "$INSTALL_DIR/squeezelite"

echo ""
echo "Done.  Verify with:"
echo "  $INSTALL_DIR/squeezelite -? | head -2"
echo "  xxd /dev/shm/squeezelite-<mac> | sed -n '6p'"
echo "     (bytes at offset 0x50 should read '45 53 55 48')"
