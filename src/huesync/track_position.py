"""Player-independent metadata channel; deliberately separate from audio analysis.

Snapshots hold a monotonic position anchor. Delivery rebases that anchor so a
new WebSocket client needs neither the server's clock nor a player-specific rule.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import stat
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote

from .lms_status import LmsPlayerStatus, _parse_status


@dataclass(frozen=True)
class TrackPosition:
    title: str | None = None
    artist: str | None = None
    position_s: float | None = None
    duration_s: float | None = None
    playing: bool = False
    observed_at: float = 0.0

    def position_now(self) -> float | None:
        if self.position_s is None:
            return None
        position = self.position_s + (max(0, time.monotonic() - self.observed_at)
                                      if self.playing else 0)
        return min(position, self.duration_s) if self.duration_s is not None else position

    def for_delivery(self) -> dict:
        result = asdict(self)
        result['position_s'] = self.position_now()
        del result['observed_at']
        return result


class TrackPositionSource(Protocol):
    """Nonblocking metadata reader. Unknown data is None.

    open starts acquisition; close awaits its completion and releases descriptors.
    read returns a stable immutable anchor, not a continuously ticking clock.
    LMS readers are session-owned; the shared AirPlay receiver reader is manager-owned.
    """
    def open(self) -> None: ...
    async def close(self) -> None: ...
    def read(self) -> TrackPosition | None: ...
    @property
    def running(self) -> bool: ...


class LmsTrackPositionSource:
    """Read-only LMS status subscription; never issues playback/sync commands.

    `tags:ad`: artist and duration; title is a standard tag. `subscribe:10`
    pushes player changes and ten-second corrections (Lyrion compound queries).
    Target selection stays with the session's existing follower, including auto.
    """
    def __init__(self, host: str, target: Callable[[], str | None], port: int = 9090):
        self._host, self._target, self._port = host, target, port
        self._task: asyncio.Task | None = None
        self._snapshot: TrackPosition | None = None
        self._selected: str | None = None

    def open(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._run(), name='lms-track-position')

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def read(self) -> TrackPosition | None:
        # Never expose the old room while the asynchronous connection switches.
        return self._snapshot if self._selected == self._target() else None

    def seed(self, target: str, status: LmsPlayerStatus, observed_at: float) -> None:
        """Reuse the session's initial status query, without another CLI request."""
        if (self._selected == target and self._snapshot
                and self._snapshot.observed_at > observed_at):
            return  # a subscription update arrived while the query was in flight
        self._selected = target
        self._snapshot = (TrackPosition(
            status.title, status.artist, status.time, status.duration,
            status.mode == 'play' and not status.waiting_to_play, observed_at,
        ) if status.mode in ('play', 'pause', 'stop') else None)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            self._task = None
        self._snapshot = None

    async def _run(self) -> None:
        while True:
            target = self._target()
            if self._selected != target:
                self._snapshot = None
            self._selected = target
            writer = None
            try:
                if target:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(self._host, self._port, limit=65536), 3)
                    writer.write(f'{target} status - 1 tags:ad subscribe:10\n'.encode())
                    await writer.drain()
                    last_received = time.monotonic()
                    while target == self._target():
                        try:
                            line = await asyncio.wait_for(reader.readline(), 1)
                        except TimeoutError:
                            if time.monotonic() - last_received > 25:
                                break
                            continue  # check local target; no additional network request
                        if not line:
                            break
                        text = line.decode('utf-8', errors='replace').strip()
                        if not text or unquote(text.split()[0]).lower() != target.lower():
                            continue
                        last_received = time.monotonic()
                        status = _parse_status(text)
                        if status.mode not in ('play', 'pause', 'stop'):
                            self._snapshot = None
                            continue
                        self._snapshot = TrackPosition(
                            status.title, status.artist, status.time, status.duration,
                            status.mode == 'play' and not status.waiting_to_play, time.monotonic())
            except (OSError, ValueError, TimeoutError):
                pass  # metadata unavailability must not interrupt audio delivery
            finally:
                self._snapshot = None
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            await asyncio.sleep(1)


class AirPlayTrackPositionSource:
    """Shairport XML/base64 DMAP FIFO: core/minm, core/asar, ssnc/prgr.

    prgr is start/current/end in wrapping 32-bit RTP frames at 44100 Hz.
    phbt (configured every ten seconds) corrects the current RTP position.
    Pause/resume/end events are supplied by the pinned Shairport implementation.
    No cover art is requested; framing is bounded even for malformed input.
    """
    MAX_ITEM = 65536

    def __init__(self, path: str = '/run/huesync/airplay.metadata'):
        self._path = Path(path)
        self._task: asyncio.Task | None = None
        self._snapshot: TrackPosition | None = None
        self._buffer = b''
        self._start: int | None = None
        self._batch: dict | None = None

    def open(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._run(), name='airplay-track-position')

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def read(self) -> TrackPosition | None:
        return self._snapshot

    def _clear(self) -> None:
        self._snapshot, self._start, self._batch, self._buffer = None, None, None, b''

    def invalidate(self) -> None:
        """Discard cached metadata when the owning manager restarts the receiver."""
        self._clear()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            self._task = None
        self._clear()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            fd = None
            try:
                fd = os.open(self._path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                    raise ValueError('Metadata path must be a FIFO')
                ready = asyncio.Event()
                loop.add_reader(fd, ready.set)
                while True:
                    await ready.wait()
                    ready.clear()
                    try:
                        chunk = os.read(fd, self.MAX_ITEM)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    self.feed(chunk)
            except (OSError, ValueError):
                pass  # missing/unavailable metadata does not stop audio
            finally:
                if fd is not None:
                    loop.remove_reader(fd)
                    os.close(fd)
                self._clear()
            await asyncio.sleep(1)

    def feed(self, chunk: bytes) -> None:
        """Incremental bounded parser; also exercised directly with captured wire shapes."""
        self._buffer += chunk
        while True:
            start = self._buffer.find(b'<item>')
            if start < 0:
                self._buffer = self._buffer[-5:]
                return
            self._buffer = self._buffer[start:]
            end = self._buffer.find(b'</item>')
            if end < 0:
                if len(self._buffer) > self.MAX_ITEM:
                    self._buffer = self._buffer[-5:]
                return
            item, self._buffer = self._buffer[:end + 7], self._buffer[end + 7:]
            if len(item) > self.MAX_ITEM:
                continue
            try:
                root = ET.fromstring(item)
                kind = bytes.fromhex(root.findtext('type', '')).decode('ascii')
                code = bytes.fromhex(root.findtext('code', '')).decode('ascii')
                data = base64.b64decode(''.join(root.findtext('data', '').split()), validate=True)
                if int(root.findtext('length', '-1')) != len(data):
                    continue
                self._item(kind, code, data.decode('utf-8', errors='replace'))
            except (ValueError, ET.ParseError, binascii.Error):
                continue

    def _update(self, **changes) -> None:
        old = self._snapshot or TrackPosition()
        self._snapshot = replace(old, position_s=old.position_now(), observed_at=time.monotonic())
        self._snapshot = replace(self._snapshot, **changes)

    def _item(self, kind: str, code: str, data: str) -> None:
        if kind == 'core' and code in ('minm', 'asar'):
            changes = {'title' if code == 'minm' else 'artist': data or None}
            if self._batch is not None:
                self._batch.update(changes)
            else:
                self._update(**changes)
        elif kind == 'ssnc':
            if code == 'mdst':
                self._batch = {'title': None, 'artist': None}
            elif code == 'mden' and self._batch is not None:
                if self._snapshot and self._snapshot.title != self._batch["title"]:
                    self._start = None
                    self._update(position_s=None, duration_s=None)
                self._update(**self._batch)
                self._batch = None
            elif code == 'prgr':
                start, current, end = (int(x) for x in data.split('/'))
                if not all(0 <= x <= 0xffffffff for x in (start, current, end)):
                    return
                elapsed, duration = (current - start) % 2**32, (end - start) % 2**32
                if duration and elapsed > duration:
                    return
                self._start = start
                self._update(position_s=elapsed / 44100,
                             duration_s=duration / 44100 if duration else None)
            elif code == 'phbt' and self._start is not None:
                current = int(data.split('/')[0])
                if 0 <= current <= 0xffffffff:
                    elapsed = ((current - self._start) % 2**32) / 44100
                    duration = self._snapshot.duration_s if self._snapshot else None
                    if duration is None or elapsed <= duration:
                        self._update(position_s=elapsed)
            elif code in ('pbeg', 'pres', 'prsm'):
                self._update(playing=True)
            elif code in ('paus', 'pfls'):
                self._update(playing=False)
            elif code in ('pend', 'aend'):
                self._snapshot, self._start, self._batch = None, None, None
