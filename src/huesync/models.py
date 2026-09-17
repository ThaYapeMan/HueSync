"""Data models for HueSync.

Kept intentionally simple (plain dataclasses, JSON-serialisable) so the
whole config can live in one human-readable, git-diffable file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .spectrum_engine import VALID_BARS_SOURCES as _VALID_BARS_SOURCES
from .spectrum_engine import VALID_ENGINE_IDS as _VALID_ENGINE_IDS

# Canonical set of onset detection method identifiers.  SyncEngine switches on
# these exact strings; any other value silently falls through to cava-based
# detection.  Keep in sync with ONSET_METHODS in web/src/lib/api.ts.
ONSET_METHODS: frozenset[str] = frozenset({"combined", "multiband", "superflux"})

# Virtual-player source type.  Keep in sync with PLAYER_TYPES in web/src/lib/api.ts.
# Adding a new type: add the enum value here, implement the canonical ingress contract,
# and add an activation branch in player_manager.activate_coupling().
class VirtualPlayerType(StrEnum):
    LMS = "LMS"
    AIRPLAY = "AirPlay"

VIRTUAL_PLAYER_TYPES: frozenset[str] = frozenset(t.value for t in VirtualPlayerType)

# Canonical set of effect IDs.  SyncEngine's _make_renderer() switches on
# these exact strings.  Keep in sync with EFFECTS in web/src/lib/api.ts.
EFFECT_IDS: frozenset[str] = frozenset({
    "spectrum_rgb",
    "spectrum_rgb_spatial",
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
        return cls(**d)


#: Current runtime Profile fields.


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
    # Name advertised to external protocols (LMS player list, AirPlay menu).
    # Falls back to player_name when empty.
    display_name: str = ""
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
    effect_type: str = "spectrum_rgb"
    effect_speed: float = 1.0
    effect_decay: float = 0.3
    blend_start: float = 0.3
    blend_end: float = 0.7
    blend_response: float = 0.1
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

    # HPSS: separate PCM signal into harmonic and percussive streams.
    # CPU cost ~1 ms/frame at 100 Hz on a Proxmox LXC (2 vCPU) — opt-in only.
    use_hpss_separation: bool = False
    bars_source: str = "cava"
    # Spectrum backend for the PCM pipeline path (AirPlay / native PCM).
    # "v2"      — HueSync V2SpectrumEngine (Hamming STFT, np.max aggregation)
    # "cavacore" — upstream cavacore via ctypes (Hann, dual FFT, bandwidth-normalised mean)
    spectrum_backend: str = "v2"

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

    def __post_init__(self) -> None:
        if self.bars_source not in _VALID_BARS_SOURCES:
            raise ValueError(
                f"Invalid bars_source {self.bars_source!r}; "
                f"must be one of {sorted(_VALID_BARS_SOURCES)}"
            )
        if self.spectrum_backend not in _VALID_ENGINE_IDS:
            raise ValueError(
                f"Invalid spectrum_backend {self.spectrum_backend!r}; "
                f"must be one of {sorted(_VALID_ENGINE_IDS)}"
            )
        # bars_source="cava" means external CAVA FIFO — spectrum_backend has no
        # meaning there.  "cavacore" specifically refers to the embedded Spectrum
        # engine over canonical PCM, so this combination is an explicit error.
        if self.bars_source == "cava" and self.spectrum_backend == "cavacore":
            raise ValueError(
                "bars_source='cava' (external CAVA FIFO) is incompatible with "
                "spectrum_backend='cavacore' (embedded cavacore Spectrum engine). "
                "Set bars_source='pcm_pipeline' to use the embedded cavacore backend."
            )

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        return cls(**d)




# ---------------------------------------------------------------------------
# Phase-2 entities: Controller, Player, Zone, Analyser,
# Effect, EnergyProfile, Coupling.
# ---------------------------------------------------------------------------


class ControllerType(StrEnum):
    HUE = "hue"
    WLED = "wled"




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

    def to_safe_dict(self) -> dict:
        """Public-API serialisation: credentials replaced by boolean presence flags.

        Ordinary entity responses hide app_key / client_key. The explicit sensitive
        full-backup download preserves them for disaster recovery.
        """
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "host": self.host,
            "app_key_configured": bool(self.app_key),
            "client_key_configured": bool(self.client_key),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Controller:
        d = dict(d)
        d["type"] = ControllerType(d.get("type", "hue"))
        return cls(**d)






@dataclass
class VirtualPlayer:
    """A virtual audio player.  type determines the audio source backend."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: VirtualPlayerType = VirtualPlayerType.LMS
    lms_host: str = "127.0.0.1"
    lms_port: int = 3483
    player_name: str = "HueSync"
    # Name advertised to external protocols (LMS player list, AirPlay menu).
    # Falls back to player_name when empty (see player_manager).
    display_name: str = ""
    player_mac: str = ""
    alsa_device: str = ""
    # MAC address of the LMS player to follow for track-mirroring.
    # When set, HueSync leaves the LMS sync group (preventing drift
    # correction) and instead mirrors track changes via the listen 1
    # event feed.  Empty string means no following — HueSync plays
    # whatever LMS sends it directly.
    follow_player_mac: str = ""
    follow_mode: str = "manual"

    def __post_init__(self) -> None:
        if self.follow_mode not in {"manual", "sync_group"}:
            raise ValueError(f"Invalid follow_mode {self.follow_mode!r}")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "lms_host": self.lms_host,
            "lms_port": self.lms_port,
            "player_name": self.player_name,
            "display_name": self.display_name,
            "player_mac": self.player_mac,
            "alsa_device": self.alsa_device,
            "follow_player_mac": self.follow_player_mac,
            "follow_mode": self.follow_mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VirtualPlayer:
        d = dict(d)
        d["type"] = VirtualPlayerType(d.get("type", VirtualPlayerType.LMS))
        return cls(**d)






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
        return cls(**d)






@dataclass
class Analyser:
    """Spectrum/onset configuration shared across Couplings; see docs/configuration.md."""

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
    # HPSS: parallel harmonic/percussive separation on the PCM tap.
    # CPU cost ~1 ms/frame at 100 Hz on a 2-vCPU LXC — disabled by default.
    use_hpss_separation: bool = False
    # "cava": existing cava/FIFO path (default for LMS players).
    # "pcm_pipeline": canonical stereo SHM v1 → shared analysis/selected engine.
    #   Same analysis pipeline as AirPlay; no external CAVA FIFO.
    bars_source: str = "cava"
    # Spectrum engine for every canonical PCM ingress (AirPlay and LMS PCM).
    # "v2"      — V2SpectrumEngine: HueSync Hamming STFT, np.max, peak EMA AGC
    # "cavacore" — upstream cavacore: Hann dual-FFT, bandwidth-normalised mean, autosens
    spectrum_backend: str = "v2"

    def __post_init__(self) -> None:
        if self.bars_source not in _VALID_BARS_SOURCES:
            raise ValueError(
                f"Invalid bars_source {self.bars_source!r}; "
                f"must be one of {sorted(_VALID_BARS_SOURCES)}"
            )
        if self.spectrum_backend not in _VALID_ENGINE_IDS:
            raise ValueError(
                f"Invalid spectrum_backend {self.spectrum_backend!r}; "
                f"must be one of {sorted(_VALID_ENGINE_IDS)}"
            )
        if self.bars_source == "cava" and self.spectrum_backend == "cavacore":
            raise ValueError(
                "bars_source='cava' (external CAVA FIFO) is incompatible with "
                "spectrum_backend='cavacore' (embedded cavacore Spectrum engine). "
                "Set bars_source='pcm_pipeline' to use the embedded cavacore backend."
            )

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
            "use_hpss_separation": self.use_hpss_separation,
            "bars_source": self.bars_source,
            "spectrum_backend": self.spectrum_backend,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Analyser:
        return cls(**d)




@dataclass
class Effect:
    """Visual output parameters (colour mode, sensitivity, …), provider-neutral.

    An Effect defines how the lights react to music. The EnergyProfile picks
    two Effects (high-energy and low-energy) and owns the blend settings.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Effect"
    effect_type: str = "spectrum_rgb"
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
            "effect_type": self.effect_type,
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
    def from_dict(cls, d: dict) -> Effect:
        return cls(**d)






@dataclass
class EnergyProfile:
    """Links a high-energy Effect and a low-energy Effect with blend parameters.

    The Coupling references one EnergyProfile. The EnergyProfile owns the two-layer
    effect selection and all LayerMixer parameters, keeping Coupling focused on
    routing (player → analysis → zone → energy_profile).
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default EnergyProfile"
    high_energy_effect_id: str = ""   # Effect used in loud passages
    low_energy_effect_id: str = ""    # Effect used in quiet passages (empty = same as high)
    blend_start: float = 0.3  # energy below this → pure low-energy (mix=0)
    blend_end: float = 0.7    # energy above this → pure high-energy (mix=1)
    blend_response: float = 0.1  # EMA alpha for mix smoothing

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "high_energy_effect_id": self.high_energy_effect_id,
            "low_energy_effect_id": self.low_energy_effect_id,
            "blend_start": self.blend_start,
            "blend_end": self.blend_end,
            "blend_response": self.blend_response,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EnergyProfile:
        return cls(**d)






@dataclass
class Coupling:
    """Links a Player + Analyser + Zone + EnergyProfile.

    Activation happens on a Coupling. The linked entities can be shared
    across multiple Couplings. Activation selects canonical PCM or external FIFO.

    The EnergyProfile owns the active Effect, optional low-energy Effect, and all
    LayerMixer blend parameters. Coupling is focused on routing:
    player → analysis → zone → energy_profile.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Coupling"
    player_id: str = ""
    analyser_id: str = ""
    zone_id: str = ""
    energy_profile_id: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "player_id": self.player_id,
            "analyser_id": self.analyser_id,
            "zone_id": self.zone_id,
            "energy_profile_id": self.energy_profile_id,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Coupling:
        return cls(**d)
