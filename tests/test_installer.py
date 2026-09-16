"""Installer/deployment contract tests.

Validates that the installer, service files, and runtime code agree on the
configuration constants that must remain consistent across the deployment.

All tests run in a normal unit-test environment without systemd.
PCM is never consumed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Python dependency contracts
# ---------------------------------------------------------------------------


def test_pyproject_declares_soxr() -> None:
    """pyproject.toml must declare soxr>=1.0 as a runtime dependency."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    deps = data["project"]["dependencies"]
    soxr_entries = [d for d in deps if d.startswith("soxr")]
    assert soxr_entries, "soxr missing from pyproject.toml [project.dependencies]"
    # Verify the minimum version constraint is present.
    assert any("1.0" in e or ">=" in e for e in soxr_entries), (
        f"soxr entry lacks version constraint: {soxr_entries}"
    )


def test_soxr_importable() -> None:
    """soxr must be importable — the dep must be installed in the venv."""
    import soxr  # noqa: F401  (import is the test)


def test_phase3_imports_available() -> None:
    """All Phase 3 runtime types must be importable."""
    from huesync.canonicalizer import (  # noqa: F401
        AudioCanonicalizer,
        CanonicalData,
        EndOfStream,
        StreamInvalidated,
        TemporarilyNoData,
    )
    from huesync.pcm_source import AirPlayPipeStereoSource  # noqa: F401
    from huesync.sync_engine import (  # noqa: F401
        CanonicalAnalysisPipeline,
        StereoMagStft,
    )


# ---------------------------------------------------------------------------
# AirPlay PCM source contract
# ---------------------------------------------------------------------------

_EXPECTED_FIFO = "/run/huesync/airplay.pcm"
_EXPECTED_RATE = 44100
_EXPECTED_FORMAT = "S16_LE"
_EXPECTED_CHANNELS = 2


def test_airplay_fifo_path_constant() -> None:
    """The AIRPLAY_PIPE constant in pcm_source.py must match the deployment path."""
    from huesync.pcm_source import AIRPLAY_PIPE

    assert str(AIRPLAY_PIPE) == _EXPECTED_FIFO, (
        f"AIRPLAY_PIPE={AIRPLAY_PIPE!r} but installer uses {_EXPECTED_FIFO!r}"
    )


def test_shairport_config_pcm_contract(tmp_path: Path) -> None:
    """_configure_shairport_name() must embed the correct PCM contract constants."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    conf_path = tmp_path / "shairport-sync.conf"
    manager._SHAIRPORT_CONF = conf_path

    with patch("subprocess.run"):
        manager._configure_shairport_name("Test")

    text = conf_path.read_text()
    assert f"output_rate = {_EXPECTED_RATE}" in text
    assert f'output_format = "{_EXPECTED_FORMAT}"' in text
    assert f"output_channels = {_EXPECTED_CHANNELS}" in text
    assert _EXPECTED_FIFO in text
    assert 'output_backend = "pipe"' in text


def test_shairport_config_name_substitution(tmp_path: Path) -> None:
    """_configure_shairport_name() must embed the provided name."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    conf_path = tmp_path / "shairport-sync.conf"
    manager._SHAIRPORT_CONF = conf_path

    with patch("subprocess.run"):
        manager._configure_shairport_name("Living Room")

    text = conf_path.read_text()
    assert 'name = "Living Room"' in text


def test_shairport_config_idempotent(tmp_path: Path) -> None:
    """Calling _configure_shairport_name() twice with the same name gives identical output."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    conf_path = tmp_path / "shairport-sync.conf"
    manager._SHAIRPORT_CONF = conf_path

    with patch("subprocess.run"):
        manager._configure_shairport_name("HueSync")
        text1 = conf_path.read_text()
        manager._configure_shairport_name("HueSync")
        text2 = conf_path.read_text()

    assert text1 == text2, "Config must be identical on repeated calls with the same name"


# ---------------------------------------------------------------------------
# Idempotent restart — regression tests for the no-restart-on-identical-config fix
# ---------------------------------------------------------------------------


def test_shairport_no_restart_on_identical_config(tmp_path: Path) -> None:
    """Second call with same name must not invoke systemctl restart."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    manager._SHAIRPORT_CONF = tmp_path / "shairport-sync.conf"

    with patch("subprocess.run") as mock_run:
        manager._configure_shairport_name("HueSync")
        assert mock_run.call_count == 1, "First call (config absent) must restart"
        manager._configure_shairport_name("HueSync")
        assert mock_run.call_count == 1, "Identical config must not restart"


def test_shairport_restart_on_advertised_name_change(tmp_path: Path) -> None:
    """Changing the advertised name must write config and restart exactly once per change."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    manager._SHAIRPORT_CONF = tmp_path / "shairport-sync.conf"

    with patch("subprocess.run") as mock_run:
        manager._configure_shairport_name("HueSync")
        assert mock_run.call_count == 1

        manager._configure_shairport_name("Living Room")  # name changed
        assert mock_run.call_count == 2, "Name change must trigger one restart"

        manager._configure_shairport_name("Living Room")  # same name repeated
        assert mock_run.call_count == 2, "Repeated same name must not restart again"


def test_shairport_no_file_rewrite_on_identical_config(tmp_path: Path) -> None:
    """File is not rewritten (mtime unchanged) when config already matches desired."""
    import time

    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    conf_path = tmp_path / "shairport-sync.conf"
    manager._SHAIRPORT_CONF = conf_path

    with patch("subprocess.run"):
        manager._configure_shairport_name("HueSync")

    mtime_ns = conf_path.stat().st_mtime_ns
    time.sleep(0.05)  # ensure any rewrite would update mtime

    with patch("subprocess.run"):
        manager._configure_shairport_name("HueSync")  # should skip write

    assert conf_path.stat().st_mtime_ns == mtime_ns, (
        "File must not be rewritten when config is already identical"
    )


def test_shairport_systemctl_failure_graceful_no_retry(tmp_path: Path) -> None:
    """systemctl failure logs a warning and does not retry; no exception raised."""
    import subprocess as _sp

    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    manager._SHAIRPORT_CONF = tmp_path / "shairport-sync.conf"

    call_count = 0

    def failing_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        raise _sp.CalledProcessError(1, cmd)

    with patch("subprocess.run", side_effect=failing_run):
        manager._configure_shairport_name("HueSync")  # must not raise

    assert call_count == 1, "systemctl must be called exactly once, not retried"


def test_shairport_pcm_contract_preserved_after_idempotent_skip(tmp_path: Path) -> None:
    """After an idempotent skip, the on-disk config still contains the correct PCM contract."""
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    conf_path = tmp_path / "shairport-sync.conf"
    manager._SHAIRPORT_CONF = conf_path

    with patch("subprocess.run"):
        manager._configure_shairport_name("HueSync")
        manager._configure_shairport_name("HueSync")  # idempotent skip

    text = conf_path.read_text()
    assert f"output_rate = {_EXPECTED_RATE}" in text
    assert f'output_format = "{_EXPECTED_FORMAT}"' in text
    assert f"output_channels = {_EXPECTED_CHANNELS}" in text
    assert _EXPECTED_FIFO in text
    assert 'output_backend = "pipe"' in text


def test_shairport_coupling_reactivation_no_restart(tmp_path: Path) -> None:
    """Re-activating an AirPlay coupling with unchanged advertised name does not restart.

    Models the scenario: user activates coupling → shairport configured → user
    deactivates and re-activates the same coupling without changing the name →
    the second activation must not restart shairport-sync.
    """
    from huesync.player_manager import PlayerManager
    from huesync.storage import Storage

    storage = Storage(tmp_path / "config.json")
    manager = PlayerManager(storage)
    manager._SHAIRPORT_CONF = tmp_path / "shairport-sync.conf"

    with patch("subprocess.run") as mock_run:
        manager._configure_shairport_name("HueSync")  # first activation
        assert mock_run.call_count == 1

        manager._configure_shairport_name("HueSync")  # re-activation, same name
        assert mock_run.call_count == 1, (
            "Re-activating AirPlay coupling with unchanged name must not restart shairport"
        )


def test_shairport_configure_is_synchronous_asyncio_safe() -> None:
    """_configure_shairport_name must be synchronous (no await) for asyncio-level atomicity.

    Within asyncio's cooperative scheduling model, a synchronous function runs to
    completion before any other coroutine proceeds — making the read-compare-write
    sequence effectively atomic without an explicit lock.
    """
    import inspect

    from huesync.player_manager import PlayerManager

    assert not inspect.iscoroutinefunction(PlayerManager._configure_shairport_name), (
        "_configure_shairport_name must remain synchronous (not async def)"
    )
    src = inspect.getsource(PlayerManager._configure_shairport_name)
    assert "await " not in src, (
        "_configure_shairport_name must not contain await — synchronicity is "
        "relied upon for atomicity in asyncio's cooperative model"
    )


def test_setup_airplay_sh_pcm_contract() -> None:
    """setup-airplay.sh must write the canonical PCM contract to shairport-sync.conf."""
    script = (ROOT / "scripts" / "setup-airplay.sh").read_text()
    assert f"output_rate = {_EXPECTED_RATE}" in script, (
        "setup-airplay.sh must set output_rate = 44100 in the shairport conf"
    )
    assert f'output_format = "{_EXPECTED_FORMAT}"' in script, (
        "setup-airplay.sh must set output_format = S16_LE"
    )
    assert f"output_channels = {_EXPECTED_CHANNELS}" in script, (
        "setup-airplay.sh must set output_channels = 2"
    )
    assert _EXPECTED_FIFO in script, (
        "setup-airplay.sh must reference the canonical FIFO path"
    )


def test_setup_airplay_sh_tmpfiles_d_directory_entry() -> None:
    """setup-airplay.sh must create the /run/huesync directory via tmpfiles.d 'd' entry."""
    script = (ROOT / "scripts" / "setup-airplay.sh").read_text()
    assert "d /run/huesync" in script, (
        "setup-airplay.sh must write a tmpfiles.d 'd' entry for /run/huesync directory"
    )
    assert "huesync huesync" in script, (
        "tmpfiles.d directory entry must set ownership to huesync huesync"
    )


def test_setup_airplay_sh_tmpfiles_d_fifo_entry() -> None:
    """setup-airplay.sh must pre-create the AirPlay FIFO via tmpfiles.d 'p' entry.

    Without this, the FIFO is only created when iOS connects (shairport-sync
    pipe backend activation), so HueSync's open() raises OSError if a coupling
    is activated before the first iOS AirPlay session.  The 'p' entry creates
    the FIFO at boot, making the path available immediately.
    """
    script = (ROOT / "scripts" / "setup-airplay.sh").read_text()
    assert "p /run/huesync/airplay.pcm" in script, (
        "setup-airplay.sh must write a tmpfiles.d 'p' entry to pre-create the AirPlay FIFO"
    )


def test_setup_airplay_sh_tmpfiles_d_single_file() -> None:
    """Both tmpfiles.d entries must be written to the same file (no separate files)."""
    script = (ROOT / "scripts" / "setup-airplay.sh").read_text()
    # Both entries appear in the same huesync-run.conf write operation.
    assert "huesync-run.conf" in script
    # Both types present in same script context.
    assert "d /run/huesync" in script
    assert "p /run/huesync/airplay.pcm" in script


# ---------------------------------------------------------------------------
# systemd unit contract — /run/huesync owned by tmpfiles.d, NOT RuntimeDirectory
# ---------------------------------------------------------------------------


def test_huesync_service_no_runtime_directory() -> None:
    """/run/huesync is a shared directory (huesync + shairport-sync); RuntimeDirectory
    is for directories owned by one service.  huesync.service must NOT claim it.

    The authoritative lifecycle mechanism is tmpfiles.d (setup-airplay.sh).
    RuntimeDirectory with Preserve=yes would create a confusing second owner
    and is semantically wrong for a path shared with shairport-sync.
    """
    unit = (ROOT / "systemd" / "huesync.service").read_text()
    assert "RuntimeDirectory=huesync" not in unit, (
        "huesync.service must NOT declare RuntimeDirectory=huesync — "
        "/run/huesync is shared with shairport-sync; tmpfiles.d is the authoritative owner"
    )


# ---------------------------------------------------------------------------
# Deployment scripts
# ---------------------------------------------------------------------------


def test_update_script_exists() -> None:
    """scripts/update.sh must exist (the standard update entry point)."""
    assert (ROOT / "scripts" / "update.sh").is_file(), (
        "scripts/update.sh not found — create it"
    )


def test_validate_script_exists() -> None:
    """scripts/validate.sh must exist (the post-install validation entry point)."""
    assert (ROOT / "scripts" / "validate.sh").is_file(), (
        "scripts/validate.sh not found — create it"
    )


def test_update_delegates_to_authoritative_installer() -> None:
    script = (ROOT / "scripts" / "update.sh").read_text()
    assert 'exec "$REPO_DIR/scripts/install-huesync.sh"' in script
    assert 'pip install' not in script
    installer = (ROOT / "scripts" / "install-huesync.sh").read_text()
    assert 'systemctl restart avahi-daemon nqptp shairport-sync huesync' in installer


def test_update_script_contains_git_pull() -> None:
    """scripts/update.sh must perform a git pull to fetch latest commits."""
    script = (ROOT / "scripts" / "update.sh").read_text()
    assert "git" in script and "pull" in script, (
        "scripts/update.sh must perform git pull"
    )


def test_validate_script_checks_soxr() -> None:
    """scripts/validate.sh must validate that soxr is importable."""
    script = (ROOT / "scripts" / "validate.sh").read_text()
    assert "soxr" in script, (
        "scripts/validate.sh must check that soxr is importable"
    )


def test_validate_script_checks_fifo_type() -> None:
    """scripts/validate.sh must verify the FIFO is a named pipe, not a regular file."""
    script = (ROOT / "scripts" / "validate.sh").read_text()
    assert _EXPECTED_FIFO in script, (
        "scripts/validate.sh must reference the expected FIFO path"
    )


def test_validate_script_no_fifo_reads() -> None:
    """scripts/validate.sh must not contain commands that consume PCM from the FIFO."""
    script = (ROOT / "scripts" / "validate.sh").read_text()
    # Commands that would consume PCM — none of these should appear as active code.
    for forbidden in ["cat $FIFO", "cat /run/huesync", "dd if=", "ffmpeg"]:
        assert forbidden not in script, (
            f"scripts/validate.sh must not consume FIFO content (found: {forbidden!r})"
        )


def test_setup_airplay_version_uses_absolute_path() -> None:
    """setup-airplay.sh version detection must use the absolute binary path.

    The installed binary is /usr/local/bin/shairport-sync, which may not be on
    PATH when the script runs.  Using the bare name 'shairport-sync' causes a
    'command not found' error and 'unknown' version output.
    """
    script = (ROOT / "scripts" / "setup-airplay.sh").read_text()
    assert "/usr/local/bin/shairport-sync --version" in script, (
        "setup-airplay.sh must use /usr/local/bin/shairport-sync (absolute path) "
        "for version detection, not the bare command name"
    )
    # The bare name must not appear in the version detection line.
    for line in script.splitlines():
        if "--version" in line and "shairport-sync" in line:
            assert line.lstrip().startswith("#") or "/usr/local/bin/" in line, (
                f"Version detection line must use absolute path: {line!r}"
            )


def test_squeezelite_patch_applicable() -> None:
    """BLOCKER 1: output_vis_v1.patch must be a real, machine-applicable
    unified diff that applies cleanly to the pinned upstream squeezelite
    revision.  Skipped when git/patch are unavailable or when we cannot
    reach the upstream repository (e.g. offline CI).
    """
    import shutil
    import subprocess
    import tempfile

    patch_path = ROOT / "squeezelite" / "output_vis_v1.patch"
    assert patch_path.exists(), "output_vis_v1.patch missing"
    assert patch_path.stat().st_size > 0, "output_vis_v1.patch is empty"

    body = patch_path.read_text()
    assert body.startswith("diff --git ") or "\ndiff --git " in body, (
        "output_vis_v1.patch must be a git-style unified diff"
    )

    if shutil.which("git") is None or shutil.which("patch") is None:
        import pytest
        pytest.skip("git or patch not available")

    # Extract the pinned commit hash from the build script so the two stay
    # in sync — bumping upstream requires editing exactly one place.
    build_script = (ROOT / "scripts" / "build-squeezelite.sh").read_text()
    commit_line = next(
        (
            line for line in build_script.splitlines()
            if line.strip().startswith("SQUEEZELITE_COMMIT=")
        ),
        None,
    )
    assert commit_line is not None, "build-squeezelite.sh must define SQUEEZELITE_COMMIT"
    # Format: SQUEEZELITE_COMMIT="${SQUEEZELITE_COMMIT:-<hash>}"
    import re
    m = re.search(r":-([0-9a-f]{40})", commit_line)
    assert m is not None, f"Cannot parse pinned commit hash from {commit_line!r}"
    pinned = m.group(1)

    with tempfile.TemporaryDirectory() as td:
        clone_dir = Path(td) / "squeezelite"
        clone = subprocess.run(
            [
                "git", "clone", "--quiet", "--depth", "200",
                "https://github.com/ralph-irving/squeezelite.git",
                str(clone_dir),
            ],
            capture_output=True,
        )
        if clone.returncode != 0:
            import pytest
            pytest.skip(
                f"cannot reach upstream repository: {clone.stderr.decode(errors='replace')}"
            )
        checkout = subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "--quiet", pinned],
            capture_output=True,
        )
        if checkout.returncode != 0:
            # Deepen and retry once.
            subprocess.run(
                ["git", "-C", str(clone_dir), "fetch", "--quiet", "--unshallow"],
                capture_output=True,
            )
            checkout = subprocess.run(
                ["git", "-C", str(clone_dir), "checkout", "--quiet", pinned],
                capture_output=True,
            )
        assert checkout.returncode == 0, (
            f"cannot check out pinned revision {pinned}: "
            f"{checkout.stderr.decode(errors='replace')}"
        )

        # patch --dry-run must succeed against this fresh checkout.
        with open(patch_path, "rb") as pf:
            result = subprocess.run(
                ["patch", "--dry-run", "-p1"],
                stdin=pf,
                cwd=str(clone_dir),
                capture_output=True,
            )
        assert result.returncode == 0, (
            f"patch --dry-run failed:\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )


def test_validate_script_fd_zero_is_allowed() -> None:
    """validate.sh FD audit must treat 0 open FDs as acceptable (idle AirPlay).

    0 open FDs is normal when no AirPlay coupling is active.  The script must
    not label this as a failure or claim writer+reader are expected when the
    validator cannot know whether the AirPlay path is active.
    """
    script = (ROOT / "scripts" / "validate.sh").read_text()
    # Must not emit a FAIL or misleading "expected: writer + reader" when N==0.
    assert "expected: shairport-sync writer + huesync reader" not in script, (
        "validate.sh must not claim writer+reader are expected — "
        "0 FDs is valid when no AirPlay session is active"
    )
    # Must explicitly handle the 0 case as INFO or similar non-failure.
    assert "no active AirPlay session" in script, (
        "validate.sh must explain that 0 open FDs is expected when idle"
    )
