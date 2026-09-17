"""Hatchling build hook: write git commit hash and compile cavacore native library.

Runs automatically during `pip install .` (editable or wheel).

Git hash and native library are generated outside the checkout and included in the wheel.
HUESYNC_BUILD_COMMIT supplies the revision for a git-archive build.

cavacore: compiles src/huesync/cavacore/cavacore.c + _bridge.c into
_libcavacore.so using gcc and libfftw3.  The standard HueSync installer
(scripts/install-huesync.sh) installs the required system packages automatically before
calling pip install:
    scripts/native-build-packages.txt
This shared Debian/Ubuntu package list also drives CI and the development/wheel
build instructions in docs/development.md. Python test dependencies are in the
dev extra, not in the production installer or wheel runtime dependencies.
libfftw3-dev pulls in the runtime FFTW3 shared library as a transitive
dependency, so no additional runtime package needs to be listed separately.
If those packages are absent a RuntimeError is raised immediately so that pip
reports the failure clearly with an actionable message.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # Mark wheel as platform-specific (contains a compiled .so).
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        self._generated = tempfile.TemporaryDirectory(prefix="huesync-build-")
        try:
            self._write_commit_file()
            self._build_cavacore()
        except Exception:
            self._generated.cleanup()
            raise
        generated = Path(self._generated.name)
        build_data.setdefault("force_include", {}).update({
            str(generated / "_commit.py"): "huesync/_commit.py",
            str(generated / "_libcavacore.so"): "huesync/cavacore/_libcavacore.so",
        })

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        self._generated.cleanup()

    def _write_commit_file(self) -> None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=self.root,
            )
            git_hash = result.stdout.strip()
        except Exception:
            git_hash = "unknown"

        git_hash = os.environ.get("HUESYNC_BUILD_COMMIT", git_hash)
        if git_hash != "unknown" and not re.fullmatch(r"[0-9a-f]{7,40}", git_hash):
            raise ValueError("HUESYNC_BUILD_COMMIT must be a Git hexadecimal revision")
        commit_file = Path(self._generated.name) / "_commit.py"
        commit_file.write_text(f'COMMIT = "{git_hash}"\n')

    def _build_cavacore(self) -> None:
        import shutil
        cava_dir = Path(self.root) / "src" / "huesync" / "cavacore"
        out = Path(self._generated.name) / "_libcavacore.so"

        # Pre-flight: fail fast with an actionable message if build tools are absent.
        # scripts/install-huesync.sh installs these before calling pip install.
        if shutil.which("gcc") is None:
            raise RuntimeError(
                "cavacore build requires gcc, which was not found in PATH.\n"
                "Run the standard HueSync installer (installs this automatically):\n"
                "  bash scripts/install-huesync.sh\n"
                "Or install manually:  apt install build-essential"
            )

        # Check that fftw3.h is reachable by verifying the compiler can find it.
        fftw_check = subprocess.run(
            ["gcc", "-x", "c", "-fsyntax-only", "-"],
            input="#include <fftw3.h>\n",
            capture_output=True,
            text=True,
        )
        if fftw_check.returncode != 0:
            raise RuntimeError(
                "cavacore build requires libfftw3-dev (fftw3.h not found by gcc).\n"
                "Run the standard HueSync installer (installs this automatically):\n"
                "  bash scripts/install-huesync.sh\n"
                "Or install manually:  apt install libfftw3-dev"
            )

        cmd = [
            "gcc",
            "-shared",
            "-fPIC",
            "-O2",
            "-o", str(out),
            str(cava_dir / "cavacore.c"),
            str(cava_dir / "_bridge.c"),
            "-lfftw3",
            "-lm",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(
                "cavacore native build failed.\n"
                "Run the standard HueSync installer:  bash scripts/install-huesync.sh\n"
                f"Command: {' '.join(cmd)}\n"
                f"Compiler output:\n{result.stderr.strip()}"
            )
