"""Core domain types for the HueSync effect engine.

These types define the contracts between the four pipeline layers:

    AudioPipeline  →  AudioFeatures  →  Renderer  →  Scene  →  Output  →  lights

No layer imports from the layer below it. In particular, nothing here
imports from hue_entertainment — that is confined to hue_output.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

MAX_16BIT = 65535


# ---------------------------------------------------------------------------
# Colour and geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Colour:
    """An RGB colour with components in the range 0.0–1.0."""

    r: float
    g: float
    b: float

    # Convenience sentinels — assigned after class definition to avoid the
    # forward-reference dance that ClassVar + frozen dataclass requires.
    BLACK: ClassVar[Colour]
    WHITE: ClassVar[Colour]

    def to_16bit(self) -> tuple[int, int, int]:
        """Convert to 16-bit unsigned integers as expected by the Hue
        Entertainment API.  Components are clamped to [0.0, 1.0] before
        conversion so callers never need to guard against minor float drift.
        """
        return (
            int(min(max(self.r, 0.0), 1.0) * MAX_16BIT),
            int(min(max(self.g, 0.0), 1.0) * MAX_16BIT),
            int(min(max(self.b, 0.0), 1.0) * MAX_16BIT),
        )

    def lerp(self, other: Colour, t: float) -> Colour:
        """Linear interpolation (t=0 → self, t=1 → other)."""
        return Colour(
            r=self.r + (other.r - self.r) * t,
            g=self.g + (other.g - self.g) * t,
            b=self.b + (other.b - self.b) * t,
        )


Colour.BLACK = Colour(0.0, 0.0, 0.0)
Colour.WHITE = Colour(1.0, 1.0, 1.0)


@dataclass(frozen=True)
class Position:
    """A point in 3-D space using the Hue Entertainment coordinate system.

    The hue-entertainment library (LightChannel.position) uses a
    tuple[float, float, float] in the same (x, y, z) order; construct with
    ``Position(*channel.position)``.  Values are normalised to roughly
    –1.0 … +1.0 per axis.
    """

    x: float
    y: float
    z: float


# ---------------------------------------------------------------------------
# Scene — contract between effects and the output driver
# ---------------------------------------------------------------------------


@runtime_checkable
class Scene(Protocol):
    """A colour field: given a position in the room and a timestamp, return
    a Colour.

    Effects produce Scenes; the OutputDriver samples them at each registered
    light's position.  This decouples effect logic from the Hue API: an
    effect has no knowledge of channel IDs, LightColorCommand, or transport
    details.
    """

    def color_at(self, position: Position, t: float) -> Colour: ...


class UniformScene:
    """A Scene that returns the same colour regardless of position.

    All three legacy ColorMode effects use this; future spatial effects
    (waves, fireworks) implement Scene directly with position-aware logic.
    """

    __slots__ = ("colour",)

    def __init__(self, colour: Colour) -> None:
        self.colour = colour

    def color_at(self, position: Position, t: float) -> Colour:  # noqa: ARG002
        return self.colour


# ---------------------------------------------------------------------------
# AudioFeatures — contract between the analyser and effects
# ---------------------------------------------------------------------------


@dataclass
class AudioFeatures:
    """Processed audio data for one analysis frame.

    Produced by an Analyser; consumed by Effects.  Fields not yet provided
    by a backend are left at their defaults so effects degrade gracefully.

    Band fields use *cumulative* slices (like LedFx's melbanks), not exclusive
    bands.  An effect that cares about bass receives a signal already containing
    it; it picks the smallest slice covering its range.

    Bar proportions assume cava configured with lower_cutoff_freq=50 and
    higher_cutoff_freq=10000, giving these approximate mappings for 30 bars:

        bass (~0–20 %):  50–350 Hz   sub-bass, kick, bass guitar
        mid  (~0–55 %):  50–2000 Hz  bass through upper mid
        full (0–100 %):  50–10000 Hz full analysed range
    """

    # Per-bar exertion values in [0.0, 1.0], after BandNormaliser AGC.
    bars: list[float]

    # Cumulative band energies (overlapping slices, not exclusive bands).
    bass: float
    mid: float
    full: float

    # Spectral centroid normalised 0.0–1.0 across the bar frame.
    # 0.0 = energy concentrated at the low end; 1.0 = at the high end.
    centroid: float

    # Onset detection.  onset=True on frames where a note or drum hit starts.
    # onset_strength is the raw spectral flux value (unnormalised).
    onset: bool = False
    onset_strength: float = 0.0

    # Per-band onset for multiband method (bass/mid/treble band boundaries set
    # by profile.bass_hz and profile.mid_hz).  All False when onset_method !=
    # "multiband".  onset = onset_bass or onset_mid or onset_treble when
    # multiband is active so that downstream code that only reads onset works.
    onset_bass: bool = False
    onset_mid: bool = False
    onset_treble: bool = False
    onset_bass_strength: float = 0.0
    onset_mid_strength: float = 0.0
    onset_treble_strength: float = 0.0

    # Filled by LibrosaAnalyser or AubioAnalyser; None when unavailable.
    # Effects must degrade gracefully (e.g. fall back to onset-triggered
    # behaviour) when these fields are None.
    beat: bool | None = None
    tempo: float | None = None

    # HPSS (Harmonic-Percussive Source Separation).
    # Requires use_hpss_separation=True in Analyser; both are 0.0 when
    # hpss_active is False.  Values are normalised fractions ∈ [0, 1] that sum
    # to ≈ 1.0 and represent each source's share of the current frame's energy.
    # Multiply by features.full to get an energy-weighted signal in [0, 1].
    hpss_active: bool = False
    percussive_energy: float = 0.0   # share that is percussive (drums, transients)
    harmonic_energy: float = 0.0     # share that is harmonic (tones, sustained)


# ---------------------------------------------------------------------------
# Renderer — contract between the colour engine and the analysis layer
# ---------------------------------------------------------------------------


@runtime_checkable
class Renderer(Protocol):
    """Transforms AudioFeatures into a Scene for one frame.

    Renderers may be stateful (decay envelopes, palette position, onset
    cooldown) and are instantiated once per active session.  Stateless
    renderers simply ignore *t* and return the same Scene shape for the same
    features.
    """

    def render(self, features: AudioFeatures, t: float) -> Scene: ...


# ---------------------------------------------------------------------------
# AudioPipeline — contract between the audio pipeline and the effect engine
# ---------------------------------------------------------------------------


@runtime_checkable
class AudioPipeline(Protocol):
    """Produces AudioFeatures from a running audio source.

    Planned implementations (in order of implementation priority):

        CavaPipeline    — cava bars via FIFO + BandNormaliser (stage 1, no deps)
        LibrosaAnalyser — adds tempo/beat from a rolling PCM buffer (ISC, stage 2)
        AubioAnalyser   — subprocess-based onset/beat (GPL-3, stage 3 if needed)
        NullAnalyser    — scripted features for unit tests
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def latest(self) -> AudioFeatures | None:
        """Return the most recent AudioFeatures, or None if no frame has
        arrived yet (e.g. cava has not written its first frame to the FIFO).
        """
        ...


# ---------------------------------------------------------------------------
# Output — contract between the effect engine and the transport layer
# ---------------------------------------------------------------------------


@runtime_checkable
class Output(Protocol):
    """Sends a rendered Scene to the physical lighting system.

    The only current implementation is HueDriver in hue_output.py.  The
    protocol exists so SyncEngine can be tested without a real Hue bridge
    by passing any object with a matching send() signature.
    """

    def send(self, scene: Scene, t: float) -> None: ...


# ---------------------------------------------------------------------------
# LatencyProbe — contract between the latency subsystem and SyncEngine
# ---------------------------------------------------------------------------


@runtime_checkable
class LatencyProbe(Protocol):
    """Estimates the current audio-to-lights delay for a listening player.

    start()/stop() bracket the probe's lifetime.  current_delay_ms() is
    synchronous, never blocks, and may be called on every frame in the hot
    loop inside SyncEngine.run().

    Implementations:
        NoLatencyProbe    — returns 0 (local squeezelite, no delay needed)
        FixedLatencyProbe — returns a constant (AirPlay, stable negotiated delay)
        UpnpPositionProbe — polls UPnP continuously (Sonos, step 3)
    """

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def current_delay_ms(self) -> int:
        """Best current estimate of the delay in milliseconds. Never blocks."""
        ...
