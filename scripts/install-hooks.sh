#!/usr/bin/env bash
# Configure git to use the committed hook scripts in .githooks/.
# Run once per clone after initial checkout.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
git -C "$REPO_ROOT" config core.hooksPath .githooks
echo "Git hooks installed from .githooks/ (pre-commit: frontend build check)."
