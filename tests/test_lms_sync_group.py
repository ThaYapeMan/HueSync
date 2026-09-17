"""Sync-group mode uses real CLI sockets and never sends playback/group commands."""
import asyncio
import contextlib
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from huesync.lms_follower import LmsSyncGroupObserver

OWN = "02:00:00:00:00:01"
MANAGED = "02:00:00:00:00:02"
ROOM = "aa:00:00:00:00:01"
OTHER = "bb:00:00:00:00:01"


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


class Server:
    def __init__(self):
        self.peers = []
        self.commands = []
        self.writer = None
        self.handlers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            line = (await reader.readline()).decode().strip()
            self.commands.append(line)
            if line == "listen 1":
                self.writer = writer
                writer.write(b"listen 1\n")
                await writer.drain()
                await reader.read()
            elif line == f"{OWN} sync ?":
                peers = quote(",".join(self.peers), safe="") if self.peers else "-"
                writer.write(f"{OWN} sync {peers}\n".encode())
                await writer.drain()
            else:
                raise AssertionError(f"Unexpected mutating/query command: {line}")
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.handlers.discard(task)

    async def event(self, line):
        self.writer.write((line + "\n").encode())
        await self.writer.drain()


def test_passive_group_observation_transitions_and_cleanup():
    async def run():
        server = Server()
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        callback = AsyncMock()
        observer = LmsSyncGroupObserver(
            "127.0.0.1", OWN, lambda: [OWN, MANAGED], callback,
            cli_port=listener.sockets[0].getsockname()[1], poll_interval=0.05,
        )
        task = observer.start()
        try:
            await until(lambda: callback.await_count > 0 and server.writer is not None)
            callback.assert_awaited_with(None)
            assert "Not synced with any player" in observer.warning
            server.peers = [ROOM.upper(), OWN, MANAGED]
            await server.event(f"{ROOM} sync {OWN}")
            await until(lambda: observer._follow_mac == ROOM)
            assert observer.warning is None
            callback.assert_awaited_with(ROOM)
            # No playback commands even for the selected peer's events.
            for event in ["playlist newsong title 0", "playlist stop", "playlist pause 1",
                          "playlist pause 0"]:
                await server.event(f"{ROOM} {event}")
            # Multi-member groups retain the current target, independent of ordering.
            count = callback.await_count
            server.peers = [OTHER, ROOM]
            await server.event(f"{OTHER} sync {OWN}")
            await asyncio.sleep(0.12)
            assert observer._follow_mac == ROOM
            assert callback.await_count == count
            # No notification: the fallback poll still notices a changed group.
            server.peers = [OTHER]
            await until(lambda: observer._follow_mac == OTHER)
            callback.assert_awaited_with(OTHER)
            server.peers = [MANAGED, OWN]
            await until(lambda: not observer._follow_mac)
            assert "No external player" in observer.warning
            server.peers = [OTHER, ROOM]
            await until(lambda: observer._follow_mac == ROOM)  # deterministic initial choice
            # Reconnection requests an immediate refresh and keeps target tracking alive.
            old_writer = server.writer
            old_writer.close()
            await until(lambda: server.writer is not old_writer)
            assert observer._follow_mac == ROOM
            server.peers = []
            await until(lambda: not observer._follow_mac)
            assert "Not synced" in observer.warning
            assert set(server.commands) == {"listen 1", f"{OWN} sync ?"}
        finally:
            observer.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            listener.close()
            await listener.wait_closed()
            await until(lambda: not server.handlers)
        assert not observer.connected
        assert not any(t.get_name() == "lms-sync-group" for t in asyncio.all_tasks())
    asyncio.run(run())


def test_query_failure_clears_target_and_recovers_without_playback():
    async def run():
        callback = AsyncMock()
        observer = LmsSyncGroupObserver("lms", OWN, lambda: [OWN], callback)
        with patch("huesync.lms_status.query_lms_sync_peers",
                   side_effect=[[ROOM], OSError("offline"), [OTHER]]) as query:
            await observer._refresh_group()
            assert observer._follow_mac == ROOM
            await observer._refresh_group()
            callback.assert_awaited_with(None)
            assert "Cannot query" in observer.warning
            await observer._refresh_group()
            callback.assert_awaited_with(OTHER)
            assert observer._follow_mac == OTHER
            query.assert_called_with("lms", OWN, 9090)
    asyncio.run(run())


def test_teardown_awaits_outstanding_sync_query():
    import threading

    async def run():
        entered = threading.Event()
        release = threading.Event()

        def query(*args):
            entered.set()
            assert release.wait(2)
            return [ROOM]

        callback = AsyncMock()
        observer = LmsSyncGroupObserver("lms", OWN, lambda: [OWN], callback)
        with patch("huesync.lms_status.query_lms_sync_peers", side_effect=query):
            refresh = asyncio.create_task(observer._refresh_group())
            await until(entered.is_set)
            refresh.cancel()
            await asyncio.sleep(0.02)
            assert not refresh.done()
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh
            callback.assert_not_awaited()
    asyncio.run(run())


def test_sync_notification_refreshes_before_fallback_poll():
    async def run():
        server = Server()
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        observer = LmsSyncGroupObserver(
            "127.0.0.1", OWN, lambda: [OWN], AsyncMock(),
            cli_port=listener.sockets[0].getsockname()[1], poll_interval=60,
        )
        task = observer.start()
        try:
            await until(lambda: observer._target_initialized and server.writer is not None)
            # Wait for the connection-triggered initial refresh to settle.
            await asyncio.sleep(0.05)
            server.peers = [ROOM]
            await server.event(f"{ROOM} sync {OWN}")
            await until(lambda: observer._follow_mac == ROOM)
            assert set(server.commands) == {"listen 1", f"{OWN} sync ?"}
        finally:
            observer.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            listener.close()
            await listener.wait_closed()
            await until(lambda: not server.handlers)
    asyncio.run(run())
