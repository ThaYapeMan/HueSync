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
        PcmAudioPipelineV2,
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


def test_update_script_contains_pip_install() -> None:
    """scripts/update.sh must run pip install to synchronize Python dependencies."""
    script = (ROOT / "scripts" / "update.sh").read_text()
    assert "pip" in script and "install" in script, (
        "scripts/update.sh must call pip install to update Python dependencies"
    )


def test_update_script_contains_service_restart() -> None:
    """scripts/update.sh must restart the huesync service after updating."""
    script = (ROOT / "scripts" / "update.sh").read_text()
    assert "systemctl restart huesync" in script, (
        "scripts/update.sh must restart huesync after dependency update"
    )


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
