"""Verify ONSET_METHODS is the canonical source of truth for the backend.

These tests guard against the frontend/backend divergence that caused the
'hfc' bug: a new onset method string added in one place but not the other
silently falls through to cava-based detection in SyncEngine without error.
"""

import re
from pathlib import Path

from huesync.models import ONSET_METHODS

_SYNC_ENGINE = Path(__file__).parent.parent / "src" / "huesync" / "sync_engine.py"


def test_onset_methods_contains_expected_values() -> None:
    assert ONSET_METHODS == {"combined", "multiband", "superflux"}


def test_onset_methods_is_frozenset() -> None:
    assert isinstance(ONSET_METHODS, frozenset)


def test_sync_engine_handles_every_onset_method() -> None:
    """Every value in ONSET_METHODS must appear as a string literal in
    sync_engine.py so we can confirm the engine actually branches on it.
    Unknown values fall through silently to cava-based detection.
    """
    source = _SYNC_ENGINE.read_text()
    for method in ONSET_METHODS - {"combined"}:
        # "combined" has no explicit branch — it's the fallthrough path.
        assert f'"{method}"' in source or f"'{method}'" in source, (
            f"onset_method {method!r} is in ONSET_METHODS but has no branch "
            f"in sync_engine.py — add it or remove it from the constant"
        )


def test_no_unknown_onset_string_literals_in_sync_engine() -> None:
    """String literals that look like onset method names in sync_engine.py
    must all be present in ONSET_METHODS (guards against typos like 'hfc').
    """
    source = _SYNC_ENGINE.read_text()
    # Find all string literals next to 'method ==' or 'method =='
    candidates = re.findall(r'method\s*==\s*["\']([^"\']+)["\']', source)
    for candidate in candidates:
        assert candidate in ONSET_METHODS, (
            f"sync_engine.py branches on onset_method={candidate!r} "
            f"but that value is not in models.ONSET_METHODS"
        )
