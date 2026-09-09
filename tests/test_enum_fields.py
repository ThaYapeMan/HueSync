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

from huesync.models import COLOUR_MODES, EFFECT_IDS, ONSET_METHODS, ColorMode

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
# colour_mode-specific: enum consistency (legacy compat)
# ---------------------------------------------------------------------------

def test_colour_modes_matches_color_mode_enum() -> None:
    """COLOUR_MODES must equal the set of ColorMode enum values — the two cannot diverge."""
    assert COLOUR_MODES == frozenset(cm.value for cm in ColorMode)


def test_colour_modes_contains_expected_values() -> None:
    assert COLOUR_MODES == {"spectrum_rgb", "mono_pulse"}


# ---------------------------------------------------------------------------
# EFFECT_IDS: all known effect IDs must be present
# ---------------------------------------------------------------------------

def test_effect_ids_contains_expected_effects() -> None:
    """EFFECT_IDS must contain all documented effect identifiers."""
    expected = {
        "spectrum_rgb", "spectrum_rgb_spatial", "mono_pulse", "pulses", "flashes",
        "splotches", "fireworks", "swirl", "wave", "solid", "none",
    }
    assert EFFECT_IDS == expected


# ---------------------------------------------------------------------------
# API PATCH handler: must return 422, not 500, for invalid effect
# ---------------------------------------------------------------------------

def test_patch_handler_has_effect_guard() -> None:
    """The PATCH /effects handler must validate effect_type against EFFECT_IDS.

    Without the guard, an unknown effect silently falls through to the
    fallback renderer instead of returning 422 Unprocessable Entity.
    """
    source = _API_PY.read_text()
    patch_section = source[source.index("patch_effect_route"):]
    next_router = patch_section.find("@router", 1)
    handler_body = patch_section[:next_router] if next_router != -1 else patch_section
    assert "EFFECT_IDS" in handler_body, (
        "patch_effect_route must check effect_type against EFFECT_IDS to return 422 "
        "for unknown effect values"
    )
