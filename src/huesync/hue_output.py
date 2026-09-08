"""Hue Entertainment output driver.

This is the only module in HueSync that imports from hue_entertainment.
Everything Hue-specific lives here: LightColorCommand construction, the
EntertainmentSession lifecycle, and channel position fetching.

The OutputDriver pattern means effects never know about channel IDs or
transport details — they produce a Scene (colour as a function of position)
and HueDriver samples it at each registered light's location.

Single-stream constraint: the Hue bridge only supports one Entertainment
stream at a time per bridge.  PlayerManager enforces this at the session
level by calling deactivate() before each activate().  HueDriver itself
has no cross-instance guard; callers are responsible for the ordering.
"""

from __future__ import annotations

from dataclasses import dataclass

from hue_entertainment import EntertainmentSession, HueEntertainmentAPI, LightColorCommand

from .models import BridgeConfig
from .types import Colour, Position, Scene

# ---------------------------------------------------------------------------
# Configuration and channel info
# ---------------------------------------------------------------------------


@dataclass
class HueOutputConfig:
    """Groups the Hue-specific output fields that live on a Profile.

    Profile still stores these as flat fields for JSON backwards
    compatibility; PlayerManager assembles a HueOutputConfig at activation
    time rather than touching the Profile's serialisation.
    """

    bridge: BridgeConfig
    area_id: str
    area_name: str


@dataclass
class ChannelInfo:
    """A single light channel with its spatial position.

    channel_id  — the Hue Entertainment channel_id used in LightColorCommand
    position    — normalised (x, y, z) from LightChannel.position; used by
                  spatial effects (waves, fireworks) to compute per-light
                  colour from a Scene.
    """

    channel_id: int
    position: Position


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Clamp *v* to [lo, hi]."""
    return max(lo, min(hi, v))


async def get_channel_infos(bridge: BridgeConfig, area_id: str) -> list[ChannelInfo]:
    """Fetch channel IDs and normalised positions for one Entertainment Area.

    Replaces the old get_area_channel_ids() which only returned IDs.
    LightChannel.position is a tuple[float, float, float] (x, y, z) set
    when the area was configured in the Hue app; defaults to (0, 0, 0) for
    lights whose position was never set.

    The Hue Entertainment API specifies positions in the range -1.0…+1.0 per
    axis.  Each component is clamped to that range here so that effect code
    can rely on normalised coordinates without additional guards.
    """
    api = HueEntertainmentAPI(bridge.host, app_key=bridge.app_key)
    try:
        areas = await api.get_entertainment_areas()
    finally:
        await api.close()
    area = next((a for a in areas if a.id == area_id), None)
    if area is None:
        raise ValueError(f"Entertainment area {area_id!r} not found on bridge {bridge.host!r}")
    return [
        ChannelInfo(
            channel_id=ch.channel_id,
            position=Position(
                x=_clamp(ch.position[0]),
                y=_clamp(ch.position[1]),
                z=_clamp(ch.position[2]),
            ),
        )
        for ch in area.channels
    ]


# ---------------------------------------------------------------------------
# HueDriver — implements the Output protocol
# ---------------------------------------------------------------------------


class HueDriver:
    """Sends rendered Scenes to a Hue Entertainment Area over DTLS/UDP.

    Lifecycle:
        driver = HueDriver(config, channels)
        await driver.start()          # opens the DTLS stream
        driver.send(scene, t)         # called at 30 Hz by SyncEngine
        await driver.stop()           # sends a final black frame, closes stream
        await driver.aclose()         # releases the connection object

    last_colours is updated on every send() call and exposed for the web UI
    preview (app.py WebSocket).  It contains one Colour per channel in the
    same order as the channels list passed to __init__.
    """

    def __init__(self, config: HueOutputConfig, channels: list[ChannelInfo]) -> None:
        self._config = config
        self._channels = channels
        self._session: EntertainmentSession | None = None
        self.last_colours: list[Colour] = []

    @property
    def channels(self) -> list[ChannelInfo]:
        return self._channels

    async def start(self) -> None:
        """Open the DTLS Entertainment stream for this area."""
        b = self._config.bridge
        self._session = EntertainmentSession(b.host, b.app_key, b.client_key)
        await self._session.start(self._config.area_id)

    def send(self, scene: Scene, t: float) -> None:
        """Sample *scene* at each channel's position and send to the bridge.

        Converts Colour.to_16bit() values into LightColorCommands.  If start()
        has not been called yet (or after stop()/aclose()), this is a no-op.
        """
        if self._session is None:
            return
        colours = [scene.color_at(ch.position, t) for ch in self._channels]
        self.last_colours = colours
        commands = [
            LightColorCommand(channel_id=ch.channel_id, red=r, green=g, blue=b)
            for ch, (r, g, b) in zip(self._channels, (c.to_16bit() for c in colours), strict=True)
        ]
        self._session.send(commands)

    async def stop(self) -> None:
        """Ask the bridge to end the stream (sends a final black frame)."""
        if self._session is not None:
            await self._session.stop()

    async def aclose(self) -> None:
        """Release the connection object.  Call after stop()."""
        if self._session is not None:
            await self._session.aclose()
            self._session = None
