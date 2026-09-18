"""Tests for LmsFollower: track-following via the LMS listen 1 CLI feed.

MockLmsServer handles three connection types in the same asyncio event loop:
  - listen 1          → long-lived event stream; test pushes events via send_newsong()
  - <mac> status …    → short-lived; returns fake URL for the followed player
  - <mac> playlist play … → short-lived; echoed and recorded for assertion

Note: asyncio.to_thread() in _mirror_track runs blocking _cli_exchange calls
in a worker thread; the event loop continues to service the mock server while
the thread does its blocking I/O, so no deadlock occurs.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock
from urllib.parse import quote as urlquote
from urllib.parse import unquote

import pytest

from huesync.lms_follower import LmsFollower, LmsSyncGroupObserver
from huesync.track_position import LmsTrackPositionSource

FOLLOW_MAC = "aa:bb:cc:dd:ee:ff"
HUESYNC_MAC = "11:22:33:44:55:66"
THIRD_MAC = "cc:dd:ee:ff:00:11"   # unrelated player; must never receive any command
MOCK_URL = "http://lms.local/music/track.flac"


class MockLmsServer:
    """Minimal LMS CLI mock for testing LmsFollower.

    Handles:
    - listen 1:            stays open; push events via send_newsong()
    - <mac> status …:      responds with MOCK_URL
    - anything else:       echoes the command (captures playlist play commands)
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self.modes = {FOLLOW_MAC: "stop", HUESYNC_MAC: "stop"}
        self.urls = {FOLLOW_MAC: MOCK_URL, HUESYNC_MAC: MOCK_URL}
        self.positions = {FOLLOW_MAC: 196.0, HUESYNC_MAC: 0.0}
        self.status_writers = {}
        self._event_writer: asyncio.StreamWriter | None = None
        self._listen_connected = asyncio.Event()
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        # Close the long-lived listen 1 writer first (unblocks _handle's reader.read()).
        # Only THEN close the server — otherwise wait_closed() would deadlock.
        if self._event_writer and not self._event_writer.is_closing():
            self._event_writer.close()
        if self._server:
            self._server.close()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line_bytes = await reader.readline()
            cmd = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            self.received.append(cmd)

            if cmd.strip() == "listen 1":
                writer.write(b"listen 1\n")
                await writer.drain()
                self._event_writer = writer
                self._listen_connected.set()
                # Block until the follower or stop() closes the connection.
                await reader.read()
                return

            mac = cmd.split()[0]
            if cmd.endswith(" mode ?"):
                writer.write(f"{mac} mode {self.modes[mac]}\n".encode())
                await writer.drain()
            elif cmd.endswith(" sync ?"):
                writer.write(f"{mac} sync {FOLLOW_MAC}\n".encode())
                await writer.drain()
            elif "tags:ad" in cmd:
                writer.write(self.status_line(mac))
                await writer.drain()
                if "subscribe:" in cmd:
                    self.status_writers[mac] = writer
                    await reader.read()
            elif "tags:u" in cmd:
                encoded_url = urlquote(self.urls[mac], safe="")
                writer.write(f"dummy status 0 1 url%3A{encoded_url}\n".encode())
                await writer.drain()
            else:
                if " playlist play " in cmd:
                    self.modes[mac] = "play"
                    self.urls[mac] = unquote(cmd.split(" playlist play ", 1)[1])
                elif " pause " in cmd:
                    self.modes[mac] = "pause" if cmd.split()[2] == "1" else "play"
                elif cmd.endswith(" stop"):
                    self.modes[mac] = "stop"
                elif " time " in cmd:
                    self.positions[mac] = float(cmd.split()[2])
                writer.write(f"{cmd}\n".encode())
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def status_line(self, mac):
        return (f"{mac} status - 1 mode:{self.modes[mac]} time:{self.positions[mac]} "
                "duration:326 title:Song artist:Artist\n").encode()

    async def transport(self, event, mode):
        self.modes[FOLLOW_MAC] = mode
        self._event_writer.write(f"{FOLLOW_MAC} {event}\n".encode())
        await self._event_writer.drain()
        writer = self.status_writers[FOLLOW_MAC]
        writer.write(self.status_line(FOLLOW_MAC))
        await writer.drain()

    async def send_newsong(self, player_mac: str, index: int = 0) -> None:
        assert self._event_writer is not None, "no active listen 1 connection"
        encoded = player_mac.replace(":", "%3A")
        self._event_writer.write(f"{encoded} playlist newsong {index}\n".encode())
        await self._event_writer.drain()

    async def send_stop(self, player_mac: str) -> None:
        assert self._event_writer is not None, "no active listen 1 connection"
        encoded = player_mac.replace(":", "%3A")
        self._event_writer.write(f"{encoded} playlist stop\n".encode())
        await self._event_writer.drain()

    async def send_pause(self, player_mac: str, state: int = 1) -> None:
        assert self._event_writer is not None, "no active listen 1 connection"
        encoded = player_mac.replace(":", "%3A")
        self._event_writer.write(f"{encoded} playlist pause {state}\n".encode())
        await self._event_writer.drain()

    def play_commands(self) -> list[str]:
        return [c for c in self.received if "playlist play" in c]

    def stop_commands(self) -> list[str]:
        return [c for c in self.received if c.strip().endswith(" stop")]

    def pause_commands(self) -> list[str]:
        return [c for c in self.received if " pause " in c]


async def _wait_for(condition, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Helper: clean follower teardown
# ---------------------------------------------------------------------------


async def _stop_follower(follower: LmsFollower, task: asyncio.Task) -> None:
    follower.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Happy path: newsong from the followed player → play command sent
# ---------------------------------------------------------------------------


def test_newsong_triggers_play() -> None:
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)
        await server.send_newsong(FOLLOW_MAC)

        assert await _wait_for(lambda: len(server.play_commands()) >= 1), (
            "no playlist play command received after newsong"
        )
        cmd = server.play_commands()[0]
        assert HUESYNC_MAC in cmd
        assert "playlist play" in cmd
        assert urlquote(MOCK_URL, safe="") in cmd

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# Newsong from a DIFFERENT player must be silently ignored
# ---------------------------------------------------------------------------


def test_newsong_from_other_player_ignored() -> None:
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)

        other_mac = "ff:ee:dd:cc:bb:aa"
        await server.send_newsong(other_mac)

        # Wait long enough for any (incorrect) reaction to arrive.
        await asyncio.sleep(0.3)
        assert len(server.play_commands()) == 0, (
            "follower should not react to newsong from a different player"
        )

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# Reconnect: follower retries after the server drops the listen 1 connection
# ---------------------------------------------------------------------------


def test_follower_reconnects_after_disconnect() -> None:
    """Follower reconnects and mirrors tracks after a listen 1 disconnect."""
    async def _test() -> None:
        connection_count = 0
        reconnect_event = asyncio.Event()
        play_event = asyncio.Event()
        status_encoded = urlquote(MOCK_URL, safe="")

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal connection_count
            try:
                line_bytes = await reader.readline()
                cmd = line_bytes.decode("utf-8", errors="replace").rstrip("\n")

                if cmd.strip() == "listen 1":
                    connection_count += 1
                    writer.write(b"listen 1\n")
                    await writer.drain()
                    if connection_count == 1:
                        # First connection: immediately close to trigger reconnect.
                        return
                    # Second connection: signal, send newsong, then wait.
                    reconnect_event.set()
                    await asyncio.sleep(0.1)
                    encoded = FOLLOW_MAC.replace(":", "%3A")
                    writer.write(f"{encoded} playlist newsong 0\n".encode())
                    await writer.drain()
                    await reader.read()  # hold open until test closes
                elif "tags:u" in cmd:
                    writer.write(f"dummy status 0 1 url%3A{status_encoded}\n".encode())
                    await writer.drain()
                else:
                    writer.write(f"{cmd}\n".encode())
                    await writer.drain()
                    if "playlist play" in cmd:
                        play_event.set()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        # Wait for reconnect (second listen 1 connection, after 1s initial backoff).
        await asyncio.wait_for(reconnect_event.wait(), timeout=3.0)

        # Follower should receive newsong and send a play command.
        assert await _wait_for(play_event.is_set, timeout=2.0), (
            "no play command after reconnect"
        )

        await _stop_follower(follower, task)
        server.close()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# Post-play unsync: sync - sent to BOTH MACs, no other player touched
# ---------------------------------------------------------------------------


def test_post_play_unsync_targets_only_huesync_and_follow_macs() -> None:
    """After a newsong event, 'sync -' is sent to HueSync's own MAC and the
    followed player's MAC only.  No other player in the LMS environment
    (e.g. THIRD_MAC) receives any command.

    This is a per-Coupling mechanism: LmsFollower knows only the two MACs
    passed to its constructor.  It makes no LMS-global changes.
    """
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)
        await server.send_newsong(FOLLOW_MAC)

        def _both_unsynced() -> bool:
            return sum(1 for c in server.received if "sync -" in c) >= 2

        assert await _wait_for(_both_unsynced, timeout=3.0), (
            "expected two 'sync -' commands (one per MAC) after play"
        )

        unsync_cmds = [c for c in server.received if "sync -" in c]

        assert any(HUESYNC_MAC in c for c in unsync_cmds), (
            f"'sync -' not sent for HueSync MAC {HUESYNC_MAC!r}; got: {unsync_cmds}"
        )
        assert any(FOLLOW_MAC in c for c in unsync_cmds), (
            f"'sync -' not sent for follow MAC {FOLLOW_MAC!r}; got: {unsync_cmds}"
        )

        # No command of any kind must target the unrelated third player.
        third_mac_cmds = [c for c in server.received if THIRD_MAC in c]
        assert not third_mac_cmds, (
            f"unrelated player {THIRD_MAC!r} was incorrectly targeted: {third_mac_cmds}"
        )

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# stop() halts the follower cleanly
# ---------------------------------------------------------------------------


def test_stop_terminates_follower() -> None:
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)

        await _stop_follower(follower, task)
        assert task.done()

        await server.stop()

    asyncio.run(_test())


# ---------------------------------------------------------------------------
# Stop propagation: master stop → HueSync stop
# Root cause: _handle_line only handled newsong; stop events were silently
# dropped, leaving HueSync playing after the master stopped.
# ---------------------------------------------------------------------------


def test_master_stop_sends_stop_to_huesync() -> None:
    """'playlist stop' from the master must propagate as a stop command to HueSync.

    Regression guard: before the fix, _handle_line only handled 'newsong'.
    A 'playlist stop' event from the master was silently ignored, leaving
    HueSync's player running indefinitely after the master stopped.
    """
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)
        await server.send_stop(FOLLOW_MAC)

        assert await _wait_for(lambda: len(server.stop_commands()) >= 1), (
            "no stop command sent to HueSync after master 'playlist stop' event"
        )
        cmd = server.stop_commands()[0]
        assert HUESYNC_MAC in cmd, (
            f"stop command targeted wrong player; expected {HUESYNC_MAC!r}, got: {cmd!r}"
        )

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


def test_stop_from_other_player_not_forwarded() -> None:
    """'playlist stop' from a player other than the master must be ignored."""
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)
        await server.send_stop(THIRD_MAC)

        await asyncio.sleep(0.3)
        assert len(server.stop_commands()) == 0, (
            "stop command sent to HueSync after a stop from an unrelated player"
        )

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


def test_master_pause_sends_pause_to_huesync() -> None:
    """'playlist pause 1' from the master propagates as 'pause 1' to HueSync.

    Also verifies that 'playlist pause 0' (resume) is forwarded correctly.
    """
    async def _test() -> None:
        server = MockLmsServer()
        port = await server.start()

        follower = LmsFollower("127.0.0.1", FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()

        await asyncio.wait_for(server._listen_connected.wait(), timeout=2.0)

        # Pause
        await server.send_pause(FOLLOW_MAC, state=1)
        assert await _wait_for(lambda: any("pause 1" in c for c in server.pause_commands())), (
            "no 'pause 1' sent to HueSync after master pause event"
        )
        pause_cmds = [c for c in server.pause_commands() if "pause 1" in c]
        assert any(HUESYNC_MAC in c for c in pause_cmds), (
            f"pause targeted wrong player; got: {pause_cmds}"
        )

        # Resume
        await server.send_pause(FOLLOW_MAC, state=0)
        assert await _wait_for(lambda: any("pause 0" in c for c in server.pause_commands())), (
            "no 'pause 0' sent to HueSync after master resume event"
        )

        await _stop_follower(follower, task)
        await server.stop()

    asyncio.run(_test())


def test_manual_connect_seeds_playback_before_newsong() -> None:
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = 'play'
        port = await server.start()
        follower = LmsFollower('127.0.0.1', FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()
        try:
            assert await _wait_for(lambda: f'{FOLLOW_MAC} sync -' in server.received)
            assert server.play_commands() == [
                f'{HUESYNC_MAC} playlist play {urlquote(MOCK_URL, safe="")}']
            assert f'{HUESYNC_MAC} sync -' in server.received
            # A subsequent newsong still takes the existing mirroring path once.
            server.urls[FOLLOW_MAC] = 'http://lms.local/next.flac'
            await server.send_newsong(FOLLOW_MAC, 1)
            assert await _wait_for(lambda: len(server.play_commands()) == 2)
            await asyncio.sleep(0.1)
            assert len(server.play_commands()) == 2
            assert server.play_commands()[1].endswith(urlquote(server.urls[FOLLOW_MAC], safe=''))
        finally:
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['stop', 'pause'])
def test_manual_connect_does_not_start_inactive_target(mode) -> None:
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = mode
        port = await server.start()
        follower = LmsFollower('127.0.0.1', FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()
        try:
            assert await _wait_for(lambda: f'{FOLLOW_MAC} mode ?' in server.received)
            await asyncio.sleep(0.1)
            assert server.play_commands() == []
        finally:
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


def test_sync_group_connect_never_seeds_or_mirrors_playback() -> None:
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = 'play'
        port = await server.start()
        follower = LmsSyncGroupObserver('127.0.0.1', HUESYNC_MAC, lambda: [HUESYNC_MAC],
                                       AsyncMock(), cli_port=port)
        task = follower.start()
        try:
            assert await _wait_for(lambda: follower.target_mac == FOLLOW_MAC)
            await server.send_newsong(FOLLOW_MAC)
            await asyncio.sleep(0.1)
            assert server.play_commands() == []
            assert not any(' mode ?' in command for command in server.received)
            assert not any(command.endswith(' sync -') for command in server.received)
        finally:
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


@pytest.mark.parametrize('own_mode,own_url,expected_plays', [
    ('play', MOCK_URL, 1), ('stop', MOCK_URL, 2),
    ('play', 'http://lms.local/wrong.flac', 2),
])
def test_manual_reconnect_skips_only_already_playing_same_url(
    own_mode, own_url, expected_plays,
) -> None:
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = 'play'
        port = await server.start()
        follower = LmsFollower('127.0.0.1', FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()
        try:
            assert await _wait_for(lambda: f'{FOLLOW_MAC} sync -' in server.received)
            server.modes[HUESYNC_MAC] = own_mode
            server.urls[HUESYNC_MAC] = own_url
            server._event_writer.close()
            assert await _wait_for(lambda: server.received.count(f'{HUESYNC_MAC} mode ?') == 2,
                                   timeout=4)
            await asyncio.sleep(0.2)
            assert len(server.play_commands()) == expected_plays
            assert server.received.count('listen 1') == 2
        finally:
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


def test_seed_seeks_current_position_only_after_play_and_newsong_never_seeks():
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = 'play'
        port = await server.start()
        follower = LmsFollower('127.0.0.1', FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        task = follower.start()
        try:
            assert await _wait_for(lambda: any(' time ' in c for c in server.received))
            seeks = [c for c in server.received if ' time ' in c]
            assert len(server.play_commands()) == len(seeks) == 1
            assert seeks[0].startswith(f'{HUESYNC_MAC} time ')
            assert 196 <= float(seeks[0].split()[2]) < 197
            assert (server.received.index(server.play_commands()[0])
                    < server.received.index(seeks[0]))
            assert server.received.index(f'{FOLLOW_MAC} sync -') < server.received.index(seeks[0])
            await server.send_newsong(FOLLOW_MAC)
            assert await _wait_for(lambda: len(server.play_commands()) == 2)
            await asyncio.sleep(0.1)
            assert [c for c in server.received if ' time ' in c] == seeks
        finally:
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


@pytest.mark.parametrize('pause,resume,stop', [
    ('playlist pause 1', 'playlist pause 0', 'playlist stop'),
    ('pause 1', 'play', 'stop'),
    ('pause', 'pause', 'stop'),
    ('pause 1 0 0', 'pause 0 0 1', 'stop'),
])
def test_manual_pause_resume_and_stop_with_followed_track_position(pause, resume, stop):
    """Two real CLI channels: room transport and subscribed track-row snapshots."""
    async def run():
        server = MockLmsServer()
        server.modes[FOLLOW_MAC] = 'play'
        port = await server.start()
        follower = LmsFollower('127.0.0.1', FOLLOW_MAC, HUESYNC_MAC, cli_port=port)
        source = LmsTrackPositionSource('127.0.0.1', lambda: follower.target_mac, port)
        source.open()
        task = follower.start()
        try:
            assert await _wait_for(lambda: any(' time ' in c for c in server.received)
                                   and FOLLOW_MAC in server.status_writers)
            server.positions[FOLLOW_MAC] = 201.0
            await server.transport(pause, 'pause')
            assert await _wait_for(lambda: server.modes[HUESYNC_MAC] == 'pause')
            assert await _wait_for(lambda: source.read() and not source.read().playing)
            assert source.read().position_now() == 201
            await asyncio.sleep(0.1)
            assert source.read().position_now() == 201
            assert source.read().title == 'Song'
            await server.transport(resume, 'play')
            assert await _wait_for(lambda: server.modes[HUESYNC_MAC] == 'play')
            assert await _wait_for(lambda: source.read().playing)
            assert source.read().position_now() >= 201
            await server.transport(stop, 'stop')
            assert await _wait_for(lambda: server.modes[HUESYNC_MAC] == 'stop')
            assert await _wait_for(lambda: not source.read().playing)
            assert source.read().position_now() == 201
        finally:
            await source.close()
            await _stop_follower(follower, task)
            await server.stop()
    asyncio.run(run())


def test_sync_group_transport_events_never_relay_or_seek():
    async def run():
        follower = LmsSyncGroupObserver('host', HUESYNC_MAC, lambda: [], AsyncMock())
        follower._follow_mac = FOLLOW_MAC
        for event in ('playlist pause 1', 'playlist pause 0', 'playlist stop',
                      'pause', 'pause 1', 'play', 'stop', 'playlist newsong 0'):
            await follower._handle_line(f'{FOLLOW_MAC} {event}')
        assert follower._play_count == 0
    # Any attempted CLI exchange fails the test, including a swallowed OSError.
    from unittest.mock import patch
    with patch('huesync.lms_follower._cli_exchange') as exchange:
        asyncio.run(run())
        exchange.assert_not_called()


@pytest.mark.parametrize('event,method,args', [
    ('pause 1', '_send_pause', (1,)),
    ('pause 0', '_send_pause', (0,)),
    ('pause 1 0 0', '_send_pause', (1,)),
    ('playlist pause 1 0 0', '_send_pause', (1,)),
    ('play', '_send_pause', (0,)),
    ('stop', '_send_stop', ()),
    ('playlist stop', '_send_stop', ()),
])
def test_documented_transport_notifications_reach_manual_relay(event, method, args):
    from unittest.mock import patch
    follower = LmsFollower('host', FOLLOW_MAC, HUESYNC_MAC)
    with patch.object(follower, method) as send:
        asyncio.run(follower._handle_line(f'{FOLLOW_MAC} {event}'))
        send.assert_called_once_with(*args)


def test_seed_alignment_waits_for_ready_and_compensates_query_delay(monkeypatch):
    from unittest.mock import Mock

    from huesync.lms_status import LmsPlayerStatus
    follower = LmsFollower('host', FOLLOW_MAC, HUESYNC_MAC)
    clock = [100.0]
    monkeypatch.setattr('huesync.lms_follower.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('huesync.lms_follower.time.sleep', lambda delay: clock.__setitem__(
        0, clock[0] + delay))
    calls = []

    def status(host, mac, port):
        calls.append(mac)
        if mac == HUESYNC_MAC:
            return LmsPlayerStatus(mode='play', waiting_to_play=len(calls) == 1)
        clock[0] += 0.25
        return LmsPlayerStatus(mode='play', time=196, duration=326)

    monkeypatch.setattr('huesync.lms_follower.query_lms_status', status)
    monkeypatch.setattr(follower, '_get_current_url', lambda: MOCK_URL)
    exchange = Mock()
    monkeypatch.setattr('huesync.lms_follower._cli_exchange', exchange)
    follower._align_seed_position(MOCK_URL)
    assert calls == [HUESYNC_MAC, HUESYNC_MAC, FOLLOW_MAC]
    exchange.assert_called_once_with('host', 9090, f'{HUESYNC_MAC} time 196.250\n')


@pytest.mark.parametrize('reason', ['unknown_position', 'paused', 'track_changed', 'stopped'])
def test_seed_alignment_does_not_seek_invalid_or_changed_source(monkeypatch, reason):
    from unittest.mock import Mock

    from huesync.lms_status import LmsPlayerStatus
    follower = LmsFollower('host', FOLLOW_MAC, HUESYNC_MAC)
    if reason == 'stopped':
        follower._stop_event.set()
    source = LmsPlayerStatus(mode='pause' if reason == 'paused' else 'play',
                             time=None if reason == 'unknown_position' else 196)
    monkeypatch.setattr('huesync.lms_follower.query_lms_status', Mock(side_effect=[
        LmsPlayerStatus(mode='play'), source]))
    monkeypatch.setattr(follower, '_get_current_url', lambda:
                        'different' if reason == 'track_changed' else MOCK_URL)
    exchange = Mock()
    monkeypatch.setattr('huesync.lms_follower._cli_exchange', exchange)
    follower._align_seed_position(MOCK_URL)
    exchange.assert_not_called()
