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
from urllib.parse import quote as urlquote

from huesync.lms_follower import LmsFollower

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

            if "tags:u" in cmd:
                encoded_url = urlquote(MOCK_URL, safe="")
                writer.write(f"dummy status 0 1 url%3A{encoded_url}\n".encode())
                await writer.drain()
            else:
                writer.write(f"{cmd}\n".encode())
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def send_newsong(self, player_mac: str, index: int = 0) -> None:
        assert self._event_writer is not None, "no active listen 1 connection"
        encoded = player_mac.replace(":", "%3A")
        self._event_writer.write(f"{encoded} playlist newsong {index}\n".encode())
        await self._event_writer.drain()

    def play_commands(self) -> list[str]:
        return [c for c in self.received if "playlist play" in c]


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
