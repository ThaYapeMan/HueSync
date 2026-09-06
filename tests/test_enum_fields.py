"""Generalized guard for enum-like string fields stored in entities.

Pattern: the frontend offers a dropdown with hardcoded string values; the backend
validates or branches on those strings.  A mismatch (frontend invents a value
the backend doesn't know) causes either a 500 (unguarded ValueError) or silent
wrong behaviour (unhandled fallthrough).

To add a new guarded field:
1. Add its canonical frozenset to models.py (or derive it from an existing enum).
2. Add a row to _FIELD_CHECKS below.
3. Add the matching constant to lib/api.ts and wire it into every dropdown.
"""

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from huesync.models import COLOUR_MODES, ONSET_METHODS, ColorMode

_ROOT = Path(__file__).parent.parent
_API_PY = _ROOT / "src" / "huesync" / "api.py"
_SYNC_ENGINE_PY = _ROOT / "src" / "huesync" / "sync_engine.py"


# ---------------------------------------------------------------------------
# Table of all guarded enum-like fields
# ---------------------------------------------------------------------------

class FieldCheck(NamedTuple):
    name: str
    canonical: frozenset[str]
    source_file: Path
    pattern: str          # regex that matches  field == "value"  usages


_FIELD_CHECKS: list[FieldCheck] = [
    FieldCheck(
        name="onset_method",
        canonical=ONSET_METHODS,
        source_file=_SYNC_ENGINE_PY,
        pattern=r'method\s*==\s*["\']([^"\']+)["\']',
    ),
    FieldCheck(
        name="color_mode (API guard)",
        canonical=COLOUR_MODES,
        source_file=_API_PY,
        # The API uses ColorMode("...") — the string literal is the arg
        pattern=r'ColorMode\(\s*["\']([^"\']+)["\']\s*\)',
    ),
]


# ---------------------------------------------------------------------------
# Parameterised tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fc", _FIELD_CHECKS, ids=lambda fc: fc.name)
def test_canonical_set_is_nonempty(fc: FieldCheck) -> None:
    assert len(fc.canonical) > 0, f"{fc.name}: canonical set must not be empty"


@pytest.mark.parametrize("fc", _FIELD_CHECKS, ids=lambda fc: fc.name)
def test_no_string_literal_outside_canonical(fc: FieldCheck) -> None:
    """String literals matched by the field pattern must all be in the canonical set."""
    source = fc.source_file.read_text()
    found = set(re.findall(fc.pattern, source))
    unknown = found - fc.canonical
    assert not unknown, (
        f"{fc.name}: {fc.source_file.name} uses values not in canonical set: "
        f"{unknown!r}.  Add them to models.py or remove them."
    )


# ---------------------------------------------------------------------------
# colour_mode-specific: enum consistency
# ---------------------------------------------------------------------------

def test_colour_modes_matches_color_mode_enum() -> None:
    """COLOUR_MODES must equal the set of ColorMode enum values — the two cannot diverge."""
    assert COLOUR_MODES == frozenset(cm.value for cm in ColorMode)


def test_colour_modes_contains_expected_values() -> None:
    assert COLOUR_MODES == {"spectrum_rgb", "mono_pulse"}


# ---------------------------------------------------------------------------
# API PATCH handler: must return 422, not 500, for invalid color_mode
# ---------------------------------------------------------------------------

def test_patch_handler_has_colormode_guard() -> None:
    """The PATCH /render-configs handler must wrap ColorMode() in try/except.

    Without the guard, an invalid color_mode raises ValueError → FastAPI
    returns 500 Internal Server Error instead of 422 Unprocessable Entity.
    """
    source = _API_PY.read_text()
    patch_section = source[source.index("patch_render_config"):]
    # Look for a try block around ColorMode() within the patch handler
    # (before the next @router decorator).
    next_router = patch_section.find("@router", 1)
    handler_body = patch_section[:next_router] if next_router != -1 else patch_section
    assert "try:" in handler_body and "ColorMode(" in handler_body, (
        "patch_render_config must wrap ColorMode() in try/except to return 422 "
        "instead of 500 for unknown color_mode values"
    )
