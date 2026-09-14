"""Hatchling build hook: write git commit hash and compile cavacore native library.

Runs automatically during `pip install .` (editable or wheel).

Git hash: if git is not available the file is written with COMMIT = "unknown".

cavacore: compiles src/huesync/cavacore/cavacore.c + _bridge.c into
_libcavacore.so using gcc and libfftw3.  The standard HueSync installer
(scripts/update.sh) installs the required system packages automatically before
calling pip install:
    build-essential   (gcc + C headers)
    libfftw3-dev      (FFTW3 development headers, build-time)
    libfftw3-3        (FFTW3 shared library, runtime)
If those packages are absent a RuntimeError is raised immediately so that pip
reports the failure clearly with an actionable message.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        # Mark wheel as platform-specific (contains a compiled .so).
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
        self._write_commit_file()
        self._build_cavacore()

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

        commit_file = Path(self.root) / "src" / "huesync" / "_commit.py"
        commit_file.write_text(f'COMMIT = "{git_hash}"\n')

    def _build_cavacore(self) -> None:
        import shutil
        cava_dir = Path(self.root) / "src" / "huesync" / "cavacore"
        out = cava_dir / "_libcavacore.so"

        # Pre-flight: fail fast with an actionable message if build tools are absent.
        # scripts/update.sh installs these before calling pip install.
        if shutil.which("gcc") is None:
            raise RuntimeError(
                "cavacore build requires gcc, which was not found in PATH.\n"
                "Run the standard HueSync installer (installs this automatically):\n"
                "  bash scripts/update.sh\n"
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
                "  bash scripts/update.sh\n"
                "Or install manually:  apt install libfftw3-dev libfftw3-3"
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
                "Run the standard HueSync installer:  bash scripts/update.sh\n"
                f"Command: {' '.join(cmd)}\n"
                f"Compiler output:\n{result.stderr.strip()}"
            )
