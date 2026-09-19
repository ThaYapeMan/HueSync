"""Track-following for HueSync's virtual LMS player.

When HueSync is removed from the LMS sync group (to prevent active drift
correction from disturbing Sonos playback), this module replaces the
implicit track-following that sync-group membership provided.

It connects to the LMS CLI's push-notification feed (listen 1) and reacts
to 'playlist newsong', 'playlist stop', and 'playlist pause' events from
the configured follow_player_mac by mirroring those commands to HueSync's
own player.

LmsSyncGroupObserver instead preserves native sync membership and uses only
read-only group queries and notifications. It never invokes mirroring helpers.

Reconnects automatically with exponential backoff on any disconnection.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Literal
from urllib.parse import quote, unquote

from .lms_status import query_lms_status

TransportAction = Literal[
    "play", "pause", "toggle", "stop", "next", "previous", "seek_forward", "seek_backward",
]
_TRANSPORT_COMMANDS = {
    "play": "play", "pause": "pause 1", "toggle": "pause", "stop": "stop",
    "next": "playlist index +1", "previous": "playlist index -1",
    "seek_forward": "time +5", "seek_backward": "time -5",
}

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

    Mirrors newsong, stop, and pause/unpause events from *follow_mac* to
    *huesync_mac*, keeping HueSync's own player in sync with the master
    without joining an LMS sync group.

    Start with :meth:`start` (returns an asyncio.Task).  Stop by calling
    :meth:`stop`; the task will exit cleanly on the next event-loop tick.

    :attr:`connected` is True while the listen-1 TCP connection is
    established.  :attr:`warning` is a human-readable string when the
    follower is disconnected (reconnecting), or None when healthy.
    """

    _MIRRORS_PLAYBACK = True

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
        self._play_count: int = 0  # diagnostic: total play commands sent this session
        self._connected: bool = False

    @property
    def target_mac(self) -> str | None:
        """Current manual or sync-group target; None while auto mode is unsynced."""
        return self._follow_mac or None

    @property
    def connected(self) -> bool:
        """True while the listen-1 TCP connection is established."""
        return self._connected

    @property
    def warning(self) -> str | None:
        """Human-readable warning when the follower is not healthy, else None."""
        if not self._connected:
            return "LMS follower not connected — reconnecting"
        return None

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
                self._connected = False
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
            self._connected = True
            log.info(
                "LMS follower: connected to %s:%d, watching %s",
                self._host,
                self._port,
                self._follow_mac,
            )

            if self._MIRRORS_PLAYBACK:
                seed = asyncio.create_task(asyncio.to_thread(self._seed_playback))
                try:
                    await asyncio.shield(seed)
                except asyncio.CancelledError:
                    # Own the bounded CLI exchanges through teardown; never let a
                    # startup query send playback after the session has stopped.
                    self._stop_event.set()
                    await seed
                    raise

            while not self._stop_event.is_set():
                line_bytes = await reader.readline()
                if not line_bytes:
                    raise ConnectionResetError("LMS closed the CLI connection")
                await self._handle_line(
                    line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                )
        finally:
            self._connected = False
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

        # Diagnostic: log ALL newsong events so we can see if the wrong player
        # is triggering, or if the same player fires more than once per track.
        if command == "playlist" and sub.startswith("newsong"):
            if player_id == self._follow_mac:
                log.info(
                    "DIAG newsong MATCH:  player=%s sub=%r -> will send play #%d",
                    player_id, sub[:40], self._play_count + 1,
                )
            else:
                log.info(
                    "DIAG newsong IGNORE: player=%s sub=%r (watching %s)",
                    player_id, sub[:40], self._follow_mac,
                )

        if command in {"pause", "stop", "play"} or (
                command == "playlist" and sub.split()[:1] in (["pause"], ["stop"])):
            log.debug("LMS transport %s: player=%s command=%s sub=%r watching=%s",
                      "MATCH" if player_id == self._follow_mac else "IGNORE",
                      player_id, command, sub[:80], self._follow_mac)

        if player_id != self._follow_mac:
            return  # event from a different player — ignore

        log.debug(
            "LMS follow event: player=%s command=%s sub=%r",
            player_id,
            command,
            sub[:80],
        )

        if not self._MIRRORS_PLAYBACK:
            return
        if command == "playlist" and sub.startswith("newsong"):
            await asyncio.to_thread(self._mirror_track)
            return

        # listen 1 carries both commands and playlist state notifications.
        # Explicit pause values are idempotent when both forms arrive.
        event = sub.split() if command == "playlist" else [command, *sub.split()]
        if not event or event[0] not in {"pause", "stop", "play"}:
            return
        log.info("LMS follower transport: player=%s event=%s", player_id, " ".join(event))
        if event[0] == "stop":
            await asyncio.to_thread(self._send_stop)
        elif event[0] == "play":
            await asyncio.to_thread(self._send_pause, 0)
        elif len(event) > 1 and event[1] in {"0", "1"}:
            # Additional fade/suppressShowBriefly parameters are not the state.
            await asyncio.to_thread(self._send_pause, int(event[1]))
        elif len(event) == 1:
            # Bare pause toggles the source, not necessarily our independent
            # player. Resolve its resulting state; never blindly toggle ours.
            mode = await asyncio.to_thread(self._query_mode, self._follow_mac)
            if mode in {"play", "pause"}:
                await asyncio.to_thread(self._send_pause, int(mode == "pause"))

    # ------------------------------------------------------------------
    # Blocking helpers (run in a thread via asyncio.to_thread)
    # ------------------------------------------------------------------

    def control_target(self, action: TransportAction, expected_mac: str) -> None:
        """Explicit user control of the room, separate from automatic mirroring.

        Also usable by the sync-group observer: it remains passive unless the
        user explicitly requests transport. Never send to our virtual player.
        """
        target = self.target_mac
        if not target or target != expected_mac or target.lower() == self._huesync_mac.lower():
            raise RuntimeError("Follow target changed or is unavailable")
        command = _TRANSPORT_COMMANDS[action]
        _cli_exchange(self._host, self._port, f"{target} {command}\n")

    def _query_mode(self, mac: str) -> str | None:
        raw = _cli_exchange(self._host, self._port, f"{mac} mode ?\n")
        parts = [unquote(part) for part in raw.split()]
        if (len(parts) == 3 and parts[0].lower() == mac.lower()
                and parts[1] == "mode" and parts[2] in {"play", "pause", "stop"}):
            return parts[2]
        return None

    def _seed_playback(self) -> None:
        """Start an already-playing track on each manual connect/reconnect."""
        try:
            if self._query_mode(self._follow_mac) == "play" and not self._stop_event.is_set():
                if url := self._mirror_track(only_if_needed=True):
                    self._align_seed_position(url)
        except OSError:
            log.warning("LMS follower: startup playback/alignment query failed; waiting for events")

    def _mirror_track(self, *, only_if_needed: bool = False) -> str | None:
        """Query the followed player's current URL and play it on HueSync."""
        url = self._get_current_url()
        if url:
            if only_if_needed:
                if (self._query_mode(self._huesync_mac) == "play"
                        and self._get_current_url(self._huesync_mac) == url):
                    return None
                if self._stop_event.is_set():
                    return None
            return url if self._send_play(url) else None

        return None

    def _align_seed_position(self, seeded_url: str) -> None:
        """Seek only a newly seeded stream, once LMS reports it playing.

        Poll readiness rather than assuming a fixed decoder startup delay. The
        CLI cannot prove audible output or seekability; target verification is
        still needed for remote streams. Never add analysis-side latency here.
        """
        deadline = time.monotonic() + 3.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            own = query_lms_status(self._host, self._huesync_mac, self._port)
            if own.mode == "play" and not own.waiting_to_play:
                observed_at = time.monotonic()
                source = query_lms_status(self._host, self._follow_mac, self._port)
                if (source.mode != "play" or source.waiting_to_play
                        or source.time is None or self._stop_event.is_set()):
                    return
                if self._get_current_url() != seeded_url or self._stop_event.is_set():
                    return  # source changed tracks during startup
                target = source.time + max(0.0, time.monotonic() - observed_at)
                if source.duration is not None and target >= source.duration:
                    return  # track ended during startup; let newsong handle it
                _cli_exchange(self._host, self._port,
                              f"{self._huesync_mac} time {target:.3f}\n")
                log.info("LMS follower seed alignment: source=%.3fs seek=%.3fs",
                         source.time, target)
                return
            time.sleep(0.1)
        log.warning("LMS follower: seeded stream not ready; position alignment skipped")

    def _get_current_url(self, mac: str | None = None) -> str | None:
        """Return the current URL for *mac* (defaults to the followed player)."""
        mac = mac or self._follow_mac
        command = f"{mac} status - 1 tags:u\n"
        log.info(
            "DIAG FOLLOW -> %s  cmd='%s status - 1 tags:u'  "
            "(play_count=%d)",
            mac,
            mac,
            self._play_count,
        )
        try:
            raw = _cli_exchange(self._host, self._port, command)
        except Exception as exc:
            log.warning(
                "LMS follower: failed to get URL for %s: %s", mac, exc
            )
            return None

        for token in raw.split():
            key_raw, sep, value_raw = token.replace("%3a", "%3A").partition("%3A")
            if sep and unquote(key_raw) == "url":
                return unquote(value_raw)

        log.warning(
            "LMS follower: no url tag in status response for %s", mac
        )
        return None

    def _post_play_unsync(self) -> None:
        """Break the sync group that 'playlist play' automatically re-forms.

        When HueSync plays the same URL as the followed player, the
        sonos-squeezebox plugin detects identical content and re-creates the
        sync group (HueSync=master, Sonos=slave) within milliseconds.
        Sending 'sync -' on both MACs immediately after the play command
        prevents LMS from correcting Sonos as a slave, which caused audible
        stuttering on the physical Sonos stream.
        """
        import time as _time
        ts = _time.strftime("%H:%M:%S")
        for label, mac in (("HueSync", self._huesync_mac), ("follow ", self._follow_mac)):
            try:
                _cli_exchange(self._host, self._port, f"{mac} sync -\n")
                log.info(
                    "DIAG UNSYNC [%s] play#%d %s (%s) sync - sent OK",
                    ts, self._play_count, label, mac,
                )
            except Exception as exc:
                log.warning(
                    "DIAG UNSYNC [%s] play#%d %s (%s) sync - FAILED: %s",
                    ts, self._play_count, label, mac, exc,
                )

    def _diag_sync_after_unsync(self) -> None:
        """Verify sync ? on both players after the post-play unsync.

        Uses 'sync ?' (not 'status') to avoid triggering side effects in
        third-party LMS plugins (e.g. sonos-squeezebox).
        """
        import time as _time
        ts = _time.strftime("%H:%M:%S")
        for label, mac in (("HueSync", self._huesync_mac), ("follow ", self._follow_mac)):
            try:
                raw = _cli_exchange(self._host, self._port, f"{mac} sync ?\n")
                peers_raw = raw.split()[2] if len(raw.split()) >= 3 else "-"
                log.info(
                    "DIAG VERIFY [%s] play#%d %s (%s) sync? -> %r",
                    ts, self._play_count, label, mac, peers_raw,
                )
            except Exception as exc:
                log.warning(
                    "DIAG VERIFY [%s] play#%d %s (%s) query failed: %s",
                    ts, self._play_count, label, mac, exc,
                )

    def _send_play(self, url: str) -> bool:
        """Tell HueSync's player to start playing *url*."""
        import time as _time
        self._play_count += 1
        encoded = quote(url, safe="")
        command = f"{self._huesync_mac} playlist play {encoded}\n"
        ts = _time.strftime("%H:%M:%S")
        log.info(
            "DIAG SENDING PLAY #%d at %s -> %s  url=%.120s",
            self._play_count, ts, self._huesync_mac, url,
        )
        try:
            _cli_exchange(self._host, self._port, command)
            log.info(
                "DIAG PLAY #%d sent OK (play_count=%d since follower start)",
                self._play_count, self._play_count,
            )
            self._post_play_unsync()
            self._diag_sync_after_unsync()
            return True
        except Exception as exc:
            log.warning("LMS follower: failed to send play command: %s", exc)
            return False

    def _send_stop(self) -> None:
        """Tell HueSync's player to stop."""
        try:
            _cli_exchange(self._host, self._port, f"{self._huesync_mac} stop\n")
            log.info("LMS follower: stop sent to HueSync (%s)", self._huesync_mac)
        except Exception as exc:
            log.warning("LMS follower: failed to send stop command: %s", exc)

    def _send_pause(self, state: int) -> None:
        """Pause (state=1) or unpause (state=0) HueSync's player."""
        try:
            _cli_exchange(self._host, self._port, f"{self._huesync_mac} pause {state}\n")
            label = "pause" if state else "unpause"
            log.info("LMS follower: %s sent to HueSync (%s)", label, self._huesync_mac)
        except Exception as exc:
            log.warning("LMS follower: failed to send %s command: %s",
                        "pause" if state else "unpause", exc)


class LmsSyncGroupObserver(LmsFollower):
    """Read-only LMS group observer; never invokes the manual mirroring helpers.

    One serialized refresh loop owns target changes. Notifications request an
    early refresh, with a five-second poll covering missed/reconnect events.
    """

    _MIRRORS_PLAYBACK = False

    def __init__(
        self, lms_host: str, huesync_mac: str,
        managed_macs: Callable[[], Iterable[str]],
        on_target_changed: Callable[[str | None], Awaitable[None]],
        cli_port: int = _DEFAULT_CLI_PORT,
        poll_interval: float = 5.0,
    ) -> None:
        super().__init__(lms_host, "", huesync_mac, cli_port)
        self._managed_macs = managed_macs
        self._on_target_changed = on_target_changed
        self._poll_interval = poll_interval
        self._refresh_requested = asyncio.Event()
        self._group_warning: str | None = "Checking LMS sync group"
        self._target_initialized = False

    @property
    def warning(self) -> str | None:
        return self._group_warning or super().warning

    async def _connect_and_listen(self) -> None:
        self._refresh_requested.set()
        await super()._connect_and_listen()

    async def _run(self) -> None:
        monitor = asyncio.create_task(self._monitor_group(), name="lms-sync-group")
        try:
            await super()._run()
        finally:
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

    async def _handle_line(self, line: str) -> None:
        # Observe only: even newsong/pause/stop must never reach the mirroring
        # dispatcher. A sync command may name either member, so refresh for any.
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {"sync", "client"}:
            self._refresh_requested.set()

    async def _monitor_group(self) -> None:
        while not self._stop_event.is_set():
            self._refresh_requested.clear()
            try:
                await self._refresh_group()
            except Exception:
                # Keep monitoring after transient probe/status failures.
                self._group_warning = "Cannot update LMS sync-group status — retrying"
                log.exception("LMS sync-group observation failed")
            try:
                await asyncio.wait_for(
                    self._refresh_requested.wait(), timeout=self._poll_interval,
                )
            except TimeoutError:
                pass

    async def _refresh_group(self) -> None:
        from .lms_status import query_lms_sync_peers

        query = asyncio.create_task(asyncio.to_thread(
            query_lms_sync_peers, self._host, self._huesync_mac, self._port,
        ))
        try:
            peers = await asyncio.shield(query)
        except asyncio.CancelledError:
            # Own the bounded blocking query until its socket has closed.
            try:
                await query
            except Exception:
                pass
            raise
        except (OSError, ValueError):
            self._group_warning = "Cannot query LMS sync group — check the LMS connection"
            await self._set_target(None)
            return

        excluded = {mac.strip().lower() for mac in self._managed_macs()}
        excluded.add(self._huesync_mac.lower())
        eligible = sorted({mac.strip().lower() for mac in peers} - excluded - {""})
        if not peers:
            self._group_warning = (
                "Not synced with any player — sync HueSync with a room in LMS to receive audio"
            )
        elif not eligible:
            self._group_warning = (
                "No external player in the LMS sync group — sync HueSync with a room in LMS"
            )
        else:
            self._group_warning = None
        target = self._follow_mac if self._follow_mac in eligible else next(iter(eligible), None)
        await self._set_target(target)

    async def _set_target(self, target: str | None) -> None:
        if not self._target_initialized or target != (self._follow_mac or None):
            # Commit only after the manager has reconciled its displayed target
            # and probe. A failed reconciliation is retried on the next refresh.
            await self._on_target_changed(target)
            self._follow_mac = target or ""
            self._target_initialized = True
