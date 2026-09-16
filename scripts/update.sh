#!/usr/bin/env bash
set -Eeuo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
git -C "$REPO_DIR" pull --ff-only
exec "$REPO_DIR/scripts/install-huesync.sh"
