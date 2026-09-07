"""Data models for HueSync.

Kept intentionally simple (plain dataclasses, JSON-serialisable) so the
whole config can live in one human-readable, git-diffable file.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, fields
from enum import StrEnum

log = logging.getLogger(__name__)


# Canonical set of onset detection method identifiers.  SyncEngine switches on
# these exact strings; any other value silently falls through to cava-based
# detection.  Keep in sync with ONSET_METHODS in web/src/lib/api.ts.
ONSET_METHODS: frozenset[str] = frozenset({"combined", "multiband", "superflux"})

# Virtual-player source type.  Only "LMS" exists now; "Sonos" will be added
# when sonos-squeezebox with OPT_VIS becomes a supported PCM source.
# Keep in sync with PLAYER_TYPES in web/src/lib/api.ts.
class VirtualPlayerType(StrEnum):
    LMS = "LMS"

VIRTUAL_PLAYER_TYPES: frozenset[str] = frozenset(t.value for t in VirtualPlayerType)

class ColorMode(StrEnum):
    """Legacy colour mode enum — kept for migration code only.

    bass_brightness was removed: it was a poorly behaved legacy mode that
    mapped bass energy to a fixed warm hue.  Profiles that stored it are
    migrated to spectrum_rgb on load.

    New code should use EFFECT_IDS and the ``effect`` field on Scene.
    """

    # Whole spectrum split in three bands (bass/mid/treble) mapped to R/G/B.
    SPECTRUM_RGB = "spectrum_rgb"
    # Single colour; brightness follows overall loudness.
    MONO_PULSE = "mono_pulse"


# Derived from ColorMode so it can never go out of sync with the enum.
# Keep in sync with COLOUR_MODES in web/src/lib/api.ts.
COLOUR_MODES: frozenset[str] = frozenset(cm.value for cm in ColorMode)

# Canonical set of effect IDs.  SyncEngine's _make_renderer() switches on
# these exact strings.  Keep in sync with EFFECTS in web/src/lib/api.ts.
EFFECT_IDS: frozenset[str] = frozenset({
    "spectrum_rgb",
    "mono_pulse",
    "pulses",
    "flashes",
    "splotches",
    "fireworks",
    "swirl",
    "wave",
    "solid",
    "none",
})


@dataclass
class BridgeConfig:
    """Credentials for one paired Hue Bridge."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Hue Bridge"
    host: str = ""
    app_key: str = ""
    client_key: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "app_key": self.app_key,
            "client_key": self.client_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BridgeConfig:
        return cls(**d)


@dataclass
class PlayerLatency:
    """Per-player latency configuration, keyed by the LMS sync-master MAC.

    Stored globally (not per-profile) because the delay belongs to the
    listening player, not to the HueSync light target.
    """

    player_mac: str
    name: str | None = None      # human-readable label, e.g. "Sonos Living Room"
    strategy: str = "fixed"      # "none" | "fixed"; "upnp" reserved for step 3
    fixed_delay_ms: int = 2000   # used when strategy == "fixed"
    # Reserved for step 3 (UpnpPositionProbe). No effect for strategy != "upnp".
    speaker_ip: str | None = None

    def to_dict(self) -> dict:
        return {
            "player_mac": self.player_mac,
            "name": self.name,
            "strategy": self.strategy,
            "fixed_delay_ms": self.fixed_delay_ms,
            "speaker_ip": self.speaker_ip,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlayerLatency:
        return cls(
            player_mac=d["player_mac"],
            name=d.get("name"),
            strategy=d.get("strategy", "fixed"),
            fixed_delay_ms=d.get("fixed_delay_ms", 2000),
            speaker_ip=d.get("speaker_ip"),
        )


#: Profile field names as a set — used to strip unknown keys when loading old
#: or future config files so cls(**d) never receives unexpected kwargs.
_PROFILE_FIELDS: frozenset[str] = frozenset()  # filled after class definition


@dataclass
class Profile:
    """One configured 'virtual player -> Hue Entertainment Area' pairing.

    Only one Profile can be *active* at a time per bridge - the Hue Bridge
    itself only supports a single Entertainment streaming session. HueSync
    enforces this in the player manager rather than letting the bridge
    reject a second stream with a confusing error.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New profile"

    # LMS / virtual player
    lms_host: str = "127.0.0.1"
    lms_port: int = 3483
    player_name: str = "HueSync"
    player_mac: str = ""  # auto-generated on first save if left empty
    # ALSA output device for the virtual player. Empty means "use the
    # default" (snd-dummy, see player_manager.DEFAULT_ALSA_DEVICE). Only
    # set this if you have a reason to point squeezelite somewhere else.
    alsa_device: str = ""

    # Hue
    bridge_id: str = ""
    entertainment_area_id: str = ""
    entertainment_area_name: str = ""
    light_count: int = 0

    # Colour mapping / effect selection
    effect: str = "spectrum_rgb"
    effect_speed: float = 1.0
    effect_decay: float = 0.3
    mix_low_threshold: float = 0.3
    mix_high_threshold: float = 0.7
    mix_ema_alpha: float = 0.1
    sensitivity: float = 1.0  # multiplier applied to bar values before mapping
    brightness_floor: float = 0.15  # minimum brightness so lights never go fully dark
    bars: int = 30  # number of cava bars (more = finer frequency detail)
    # Analysed frequency range written into cava's [general] section.
    # Music has almost no energy above ~12 kHz; cava's default of 22000 Hz
    # (Nyquist for 44.1 kHz) leaves the top half of the bar frame near zero.
    # Option names verified against cava 0.10.7 (general:lower_cutoff_freq /
    # general:higher_cutoff_freq).
    lower_cutoff_freq: int = 50
    higher_cutoff_freq: int = 12000
    # Band boundary frequencies for SPECTRUM_RGB mode (Hz).
    # bass covers lower_cutoff_freq .. bass_hz, mid covers bass_hz .. mid_hz,
    # treble covers mid_hz .. higher_cutoff_freq.
    bass_hz: int = 250
    mid_hz: int = 2000

    # Onset detection tuning (Dixon 2006 three-condition peak-picking).
    # onset_delta: margin above the asymmetric local mean required for condition 2.
    # Higher values = fewer, more confident onsets.
    onset_delta: float = 0.1
    # onset_alpha: per-frame decay of the adaptive suppression threshold (condition 3).
    # Higher values = longer suppression after a loud onset.  Range 0–1.
    onset_alpha: float = 0.9
    # onset_method: which ODF is used for onset detection.
    #   "combined"  — full-spectrum spectral flux on cava bars (default, 30 Hz)
    #   "multiband" — per-band flux on 100 Hz STFT data; fills onset_bass/mid/treble
    #   "superflux" — Böck & Widmer (2013) SuperFlux on 100 Hz STFT data
    onset_method: str = "combined"
    # SuperFlux parameters (used only when onset_method == "superflux").
    superflux_mu: int = 3   # max-filter half-width in FFT bins
    superflux_lag: int = 2  # compare frame n with frame n-lag

    # Three-layer loudness pipeline:
    #   1. exertion_clip (HERE): sets "maximally loud" in relative terms.
    #      Steady-state music at exertion ≈ 1× maps to byte ≈ 255/clip.
    #      Higher = more headroom before saturation.
    #   2. sensitivity: multiplier applied after normalisation; fine-tune
    #      overall brightness without changing the dynamic range.
    #   3. Clip at 1.0 (in ColourModeEffect): safety ceiling before RGB
    #      conversion. Not a musical choice — do not touch for tuning.
    exertion_clip: float = 3.0
    # White-flash strength applied to the Hue output on every onset frame.
    # 0.0 = no flash (default). 1.0 = full white on onset.
    # Lerps from the current colour toward white: c_out = c + fi*(1-c).
    onset_flash_intensity: float = 0.0

    enabled: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        d = dict(d)

        # Migrate legacy color_mode field to effect.
        if "color_mode" in d and "effect" not in d:
            cm_val = d.pop("color_mode")
            # Map legacy ColorMode values; unknown values fall back to spectrum_rgb.
            if cm_val in ("spectrum_rgb", "mono_pulse"):
                d["effect"] = cm_val
            else:
                log.warning(
                    "Unsupported color_mode %r in saved profile; falling back to spectrum_rgb",
                    cm_val,
                )
                d["effect"] = "spectrum_rgb"
        elif "color_mode" in d:
            d.pop("color_mode")

        # Strip keys that are not current Profile fields so that loading a
        # config written by a newer version of HueSync never causes a
        # TypeError, and loading a config with removed fields is harmless.
        d = {k: v for k, v in d.items() if k in _PROFILE_FIELDS}

        return cls(**d)


_PROFILE_FIELDS = frozenset(f.name for f in fields(Profile))


# ---------------------------------------------------------------------------
# Phase-2 entities: Controller, Player, Zone, AnalysisConfig,
# Scene, Crossfader, Coupling.
# ---------------------------------------------------------------------------


class ControllerType(StrEnum):
    HUE = "hue"
    WLED = "wled"


_CONTROLLER_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class Controller:
    """One paired light controller (Hue Bridge, WLED device, …)."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Controller"
    type: ControllerType = ControllerType.HUE
    host: str = ""
    app_key: str = ""
    client_key: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "host": self.host,
            "app_key": self.app_key,
            "client_key": self.client_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Controller:
        d = dict(d)
        d["type"] = ControllerType(d.get("type", "hue"))
        return cls(**{k: v for k, v in d.items() if k in _CONTROLLER_FIELDS})


_CONTROLLER_FIELDS = frozenset(f.name for f in fields(Controller))


_VIRTUAL_PLAYER_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class VirtualPlayer:
    """A squeezelite virtual player connected to LMS."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: VirtualPlayerType = VirtualPlayerType.LMS
    lms_host: str = "127.0.0.1"
    lms_port: int = 3483
    player_name: str = "HueSync"
    player_mac: str = ""
    alsa_device: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "lms_host": self.lms_host,
            "lms_port": self.lms_port,
            "player_name": self.player_name,
            "player_mac": self.player_mac,
            "alsa_device": self.alsa_device,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VirtualPlayer:
        d = dict(d)
        d.pop("name", None)  # silently drop legacy name field
        d["type"] = VirtualPlayerType(d.get("type", VirtualPlayerType.LMS))
        return cls(**{k: v for k, v in d.items() if k in _VIRTUAL_PLAYER_FIELDS})


_VIRTUAL_PLAYER_FIELDS = frozenset(f.name for f in fields(VirtualPlayer))


_ZONE_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class Zone:
    """One output target within a Controller (e.g., a Hue Entertainment Area).

    Zone is the sound-to-light term (from ENTTEC EMU and similar software).
    It maps to a Hue Entertainment Area within a Controller.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Zone"
    controller_id: str = ""
    entertainment_area_id: str = ""
    entertainment_area_name: str = ""
    light_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "controller_id": self.controller_id,
            "entertainment_area_id": self.entertainment_area_id,
            "entertainment_area_name": self.entertainment_area_name,
            "light_count": self.light_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Zone:
        return cls(**{k: v for k, v in d.items() if k in _ZONE_FIELDS})


_ZONE_FIELDS = frozenset(f.name for f in fields(Zone))


_ANALYSIS_CONFIG_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class AnalysisConfig:
    """cava spectrum + onset detection parameters, shared across Couplings."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Analysis"
    onset_method: str = "combined"
    onset_delta: float = 0.1
    onset_alpha: float = 0.9
    superflux_mu: int = 3
    superflux_lag: int = 2
    bars: int = 30
    lower_cutoff_freq: int = 50
    higher_cutoff_freq: int = 12000

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "onset_method": self.onset_method,
            "onset_delta": self.onset_delta,
            "onset_alpha": self.onset_alpha,
            "superflux_mu": self.superflux_mu,
            "superflux_lag": self.superflux_lag,
            "bars": self.bars,
            "lower_cutoff_freq": self.lower_cutoff_freq,
            "higher_cutoff_freq": self.higher_cutoff_freq,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnalysisConfig:
        return cls(**{k: v for k, v in d.items() if k in _ANALYSIS_CONFIG_FIELDS})


_ANALYSIS_CONFIG_FIELDS = frozenset(f.name for f in fields(AnalysisConfig))


_SCENE_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class Scene:
    """Visual output parameters (colour mode, sensitivity, …), provider-neutral.

    A Scene describes a single colour rendering mode. The Crossfader picks two
    Scenes (active and mellow) and owns the crossfade settings.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Scene"
    effect: str = "spectrum_rgb"
    effect_speed: float = 1.0
    effect_decay: float = 0.3
    sensitivity: float = 1.0
    brightness_floor: float = 0.15
    bass_hz: int = 250
    mid_hz: int = 2000
    exertion_clip: float = 3.0
    onset_flash_intensity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "effect": self.effect,
            "effect_speed": self.effect_speed,
            "effect_decay": self.effect_decay,
            "sensitivity": self.sensitivity,
            "brightness_floor": self.brightness_floor,
            "bass_hz": self.bass_hz,
            "mid_hz": self.mid_hz,
            "exertion_clip": self.exertion_clip,
            "onset_flash_intensity": self.onset_flash_intensity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Scene:
        d = dict(d)
        # Migrate legacy color_mode field to effect.
        if "color_mode" in d and "effect" not in d:
            cm_val = d.pop("color_mode")
            if cm_val in ("spectrum_rgb", "mono_pulse"):
                d["effect"] = cm_val
            else:
                log.warning(
                    "Unsupported color_mode %r in saved Scene; falling back to spectrum_rgb",
                    cm_val,
                )
                d["effect"] = "spectrum_rgb"
        elif "color_mode" in d:
            d.pop("color_mode")
        # Old keys (mellow_colour_mode, mix_low_threshold, mix_high_threshold,
        # mix_ema_alpha) are silently dropped by the field filter below.
        return cls(**{k: v for k, v in d.items() if k in _SCENE_FIELDS})


_SCENE_FIELDS = frozenset(f.name for f in fields(Scene))


_CROSSFADER_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class Crossfader:
    """Links an active Scene and a mellow Scene with crossfade parameters.

    The Coupling references one Crossfader. The Crossfader owns the two-layer
    scene selection and all LayerMixer parameters, keeping Coupling focused on
    routing (player → analysis → zone → crossfader).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Crossfader"
    active_scene_id: str = ""   # Scene used in loud passages
    mellow_scene_id: str = ""   # Scene used in quiet passages (empty = same as active)
    low_threshold: float = 0.3  # energy below this → pure mellow (mix=0)
    high_threshold: float = 0.7  # energy above this → pure active (mix=1)
    fade_speed: float = 0.1     # EMA alpha for mix smoothing (was mix_ema_alpha)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "active_scene_id": self.active_scene_id,
            "mellow_scene_id": self.mellow_scene_id,
            "low_threshold": self.low_threshold,
            "high_threshold": self.high_threshold,
            "fade_speed": self.fade_speed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Crossfader:
        return cls(**{k: v for k, v in d.items() if k in _CROSSFADER_FIELDS})


_CROSSFADER_FIELDS = frozenset(f.name for f in fields(Crossfader))


_COUPLING_FIELDS: frozenset[str] = frozenset()  # filled after class


@dataclass
class Coupling:
    """Links a Player + AnalysisConfig + Zone + Crossfader.

    Activation happens on a Coupling. The linked entities can be shared
    across multiple Couplings, but each Coupling runs its own cava process.

    The Crossfader owns the active Scene, optional mellow Scene, and all
    LayerMixer crossfade parameters. Coupling is focused on routing:
    player → analysis → zone → crossfader.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Coupling"
    player_id: str = ""
    analysis_config_id: str = ""
    zone_id: str = ""
    crossfader_id: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "player_id": self.player_id,
            "analysis_config_id": self.analysis_config_id,
            "zone_id": self.zone_id,
            "crossfader_id": self.crossfader_id,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Coupling:
        d = dict(d)
        # Backward compat: light_provider_id → zone_id
        if "light_provider_id" in d and "zone_id" not in d:
            d["zone_id"] = d.pop("light_provider_id")
        elif "light_provider_id" in d:
            d.pop("light_provider_id")
        # Old render_config_id and mix fields are dropped here; Storage.migrate()
        # creates a Crossfader from them before Coupling.from_dict() is called.
        return cls(**{k: v for k, v in d.items() if k in _COUPLING_FIELDS})


_COUPLING_FIELDS = frozenset(f.name for f in fields(Coupling))
