"""Track-following for HueSync's virtual LMS player.

When HueSync is removed from the LMS sync group (to prevent active drift
correction from disturbing Sonos playback), this module replaces the
implicit track-following that sync-group membership provided.

It connects to the LMS CLI's push-notification feed (listen 1) and reacts
to 'playlist newsong' events from the configured follow_player_mac by
issuing a 'playlist play' command to HueSync's own player.

Reconnects automatically with exponential backoff on any disconnection.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import quote, unquote

log = logging.getLogger(__name__)

_DEFAULT_CLI_PORT = 9090
_SOCKET_TIMEOUT_S = 3.0

# TCP keepalive — detects silently dead connections without explicit close.
_KEEPALIVE_IDLE_S = 60
_KEEPALIVE_INTVL_S = 10
_KEEPALIVE_CNT = 5

_BACKOFF_INITIAL_S = 1.0
_BACKOFF_FACTOR = 2.0
_BACKOFF_MAX_S = 60.0


def _recv_line(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks)


def _cli_exchange(host: str, port: int, command: str) -> str:
    """Open a short-lived CLI connection, send *command*, return the response."""
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(command.encode("utf-8"))
        return _recv_line(sock).decode("utf-8", errors="replace").strip()


def _apply_tcp_keepalive(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for opt, val in (
        (getattr(socket, "TCP_KEEPIDLE",  None), _KEEPALIVE_IDLE_S),
        (getattr(socket, "TCP_KEEPINTVL", None), _KEEPALIVE_INTVL_S),
        (getattr(socket, "TCP_KEEPCNT",   None), _KEEPALIVE_CNT),
    ):
        if opt is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, opt, val)
            except OSError:
                pass


class LmsFollower:
    """Follows a target LMS player via the listen 1 CLI push-notification feed.

    On a 'playlist newsong' event from *follow_mac*, queries that player's
    current track URL and sends a 'playlist play' command to *huesync_mac*,
    mirroring the audio stream without joining an LMS sync group.

    Start with :meth:`start` (returns an asyncio.Task).  Stop by calling
    :meth:`stop`; the task will exit cleanly on the next event-loop tick.
    """

    def __init__(
        self,
        lms_host: str,
        follow_mac: str,
        huesync_mac: str,
        cli_port: int = _DEFAULT_CLI_PORT,
    ) -> None:
        self._host = lms_host
        self._port = cli_port
        self._follow_mac = follow_mac.lower()
        self._huesync_mac = huesync_mac
        self._stop_event = asyncio.Event()

    def start(self) -> asyncio.Task:
        """Start the follower loop and return the background task."""
        self._stop_event.clear()
        return asyncio.create_task(self._run(), name="lms-follower")

    def stop(self) -> None:
        """Signal the follower to stop after the current iteration."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Internal coroutines
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
                backoff = _BACKOFF_INITIAL_S  # reset on clean exit
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "LMS follower disconnected (%s); reconnecting in %.0f s",
                    exc,
                    backoff,
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=backoff
                    )
                    return  # stop was set during back-off sleep
                except TimeoutError:
                    pass
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX_S)

    async def _connect_and_listen(self) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        raw_sock = writer.get_extra_info("socket")
        if raw_sock is not None:
            _apply_tcp_keepalive(raw_sock)

        try:
            writer.write(b"listen 1\n")
            await writer.drain()
            log.info(
                "LMS follower: connected to %s:%d, watching %s",
                self._host,
                self._port,
                self._follow_mac,
            )

            while not self._stop_event.is_set():
                line_bytes = await reader.readline()
                if not line_bytes:
                    raise ConnectionResetError("LMS closed the CLI connection")
                await self._handle_line(
                    line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_line(self, line: str) -> None:
        """Dispatch one push-notification line from the listen 1 feed."""
        parts = line.split(" ", 2)
        if len(parts) < 2:
            return

        player_id = unquote(parts[0]).lower()
        command = parts[1]
        sub = parts[2] if len(parts) > 2 else ""

        if player_id != self._follow_mac:
            return  # event from a different player — ignore

        log.debug(
            "LMS follow event: player=%s command=%s sub=%r",
            player_id,
            command,
            sub[:80],
        )

        if command == "playlist" and sub.startswith("newsong"):
            log.info(
                "LMS follower: newsong from %s — mirroring to %s",
                self._follow_mac,
                self._huesync_mac,
            )
            await asyncio.to_thread(self._mirror_track)

    # ------------------------------------------------------------------
    # Blocking helpers (run in a thread via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _mirror_track(self) -> None:
        """Query the followed player's current URL and play it on HueSync."""
        url = self._get_current_url()
        if url:
            self._send_play(url)

    def _get_current_url(self) -> str | None:
        """Return the URL of the track currently loaded on *follow_mac*."""
        command = f"{self._follow_mac} status - 1 tags:u\n"
        try:
            raw = _cli_exchange(self._host, self._port, command)
        except Exception as exc:
            log.warning(
                "LMS follower: failed to get URL for %s: %s", self._follow_mac, exc
            )
            return None

        for token in raw.split():
            key_raw, sep, value_raw = token.replace("%3a", "%3A").partition("%3A")
            if sep and unquote(key_raw) == "url":
                return unquote(value_raw)

        log.warning(
            "LMS follower: no url tag in status response for %s", self._follow_mac
        )
        return None

    def _send_play(self, url: str) -> None:
        """Tell HueSync's player to start playing *url*."""
        encoded = quote(url, safe="")
        command = f"{self._huesync_mac} playlist play {encoded}\n"
        try:
            _cli_exchange(self._host, self._port, command)
            log.info(
                "LMS follower: told %s to play %.100s", self._huesync_mac, url
            )
        except Exception as exc:
            log.warning("LMS follower: failed to send play command: %s", exc)
