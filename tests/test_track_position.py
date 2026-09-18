"""Real read-only CLI subscription and FIFO wire protocols, with generic snapshots."""
import asyncio
import base64
import os
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from huesync.lms_follower import LmsFollower, LmsSyncGroupObserver
from huesync.lms_status import _parse_status
from huesync.models import Profile
from huesync.player_manager import ActiveSession, PlayerManager
from huesync.storage import Storage
from huesync.track_position import AirPlayTrackPositionSource, LmsTrackPositionSource, TrackPosition


def item(code, data='', kind='ssnc'):
    payload = data.encode()
    return (f'<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>'
            f'<length>{len(payload)}</length><data encoding="base64">'
            f'{base64.b64encode(payload).decode()}</data></item>\n').encode()


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_lms_metadata_tags_and_invalid_clocks():
    status = _parse_status('p status - 1 ' + ' '.join(quote(x, safe='') for x in [
        'title:Song: One', 'artist:An Artist', 'time:83.25', 'duration:225', 'mode:play']))
    assert (status.title, status.artist, status.time, status.duration, status.mode) == (
        'Song: One', 'An Artist', 83.25, 225, 'play')
    status = _parse_status('time:NaN duration:inf waitingToPlay:1')
    assert status.time is None and status.duration is None and status.waiting_to_play


def test_lms_subscription_tracks_dynamic_target_without_playback_commands():
    async def run():
        target = ['aa:bb:cc:00:00:01']
        commands, writers, handlers = [], {}, set()

        async def handle(reader, writer):
            handlers.add(asyncio.current_task())
            try:
                command = (await reader.readline()).decode().strip()
                commands.append(command)
                mac = command.split()[0]
                writers[mac] = writer
                writer.write((f'{mac} status - 1 mode:play time:83 duration:225 '
                              f'title:{mac} artist:A%20Band\n').encode())
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(asyncio.current_task())

        listener = await asyncio.start_server(handle, '127.0.0.1', 0)
        source = LmsTrackPositionSource('127.0.0.1', lambda: target[0],
                                        listener.sockets[0].getsockname()[1])
        source.open()
        try:
            await until(lambda: source.read() is not None)
            assert source.read().artist == 'A Band'
            first = target[0]
            writers[first].write(f'{first} status - 1 mode:pause time:90 duration:225\n'.encode())
            await writers[first].drain()
            await until(lambda: not source.read().playing)
            assert source.read().position_now() == 90
            target[0] = 'aa:bb:cc:00:00:02'
            assert source.read() is None
            await until(lambda: source.read() is not None)
            assert source.read().title == target[0]
            assert commands == [f'{mac} status - 1 tags:ad subscribe:10'
                                for mac in (first, target[0])]
            target[0] = None
            assert source.read() is None
        finally:
            await source.close()
            await source.close()
            listener.close()
            await listener.wait_closed()
            await until(lambda: not handlers)
        assert not source.running
    asyncio.run(run())


def test_airplay_metadata_wrap_pause_seek_and_end(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('huesync.track_position.time.monotonic', lambda: clock[0])
    source = AirPlayTrackPositionSource()
    message = item('mdst') + item('minm', 'Song & One', 'core') + item('asar', 'Artist', 'core')
    for offset in range(0, len(message), 7):
        source.feed(message[offset:offset + 7])
    assert source.read() is None  # metadata batch is committed at mden
    source.feed(item('mden') + item('pbeg'))
    start = 2**32 - 44100
    source.feed(item('prgr', f'{start}/44100/{44100 * 9}'))
    assert source.read().for_delivery() == dict(
        title='Song & One', artist='Artist', position_s=2.0, duration_s=10.0, playing=True)
    clock[0] += 3
    assert source.read().position_now() == 5
    source.feed(item('paus'))
    clock[0] += 10
    assert source.read().position_now() == 5
    source.feed(item('prgr', f'{start}/0/{44100 * 9}') + item('pres'))
    assert source.read().position_now() == 1
    source.feed(item('phbt', f'{44100 * 3}/999999999'))
    assert source.read().position_now() == 4
    clock[0] += 100
    assert source.read().position_now() == 10  # clamps, never past duration
    source.feed(item('pend'))
    assert source.read() is None


def test_airplay_unknown_duration_invalid_and_bounded_input():
    source = AirPlayTrackPositionSource()
    source.feed(item('minm', 'Live radio', 'core') + item('pbeg'))
    assert source.read().duration_s is None and source.read().position_s is None
    before = source.read()
    source.feed(item('prgr', 'broken') + item('prgr', '-1/10/20'))
    source.feed(b'<item><type>bad hex</type></item>')
    assert source.read() == before
    source.feed(b'<item>' + b'x' * (source.MAX_ITEM * 2))
    assert len(source._buffer) <= source.MAX_ITEM
    source.feed(item('mdst') + item('minm', 'Next', 'core') + item('mden'))
    assert source.read().title == 'Next' and source.read().artist is None


def test_airplay_real_fifo_lifecycle_and_session_teardown(tmp_path):
    async def run():
        path = tmp_path / 'metadata'
        os.mkfifo(path, 0o600)
        source = AirPlayTrackPositionSource(str(path))
        source.open()
        # Opening the writer only after the reader task has opened the FIFO.
        await asyncio.sleep(0.01)
        writer = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(writer, item('minm', 'FIFO title', 'core') + item('pbeg') +
                     item('prgr', '0/44100/441000'))
            await until(lambda: source.read() is not None and source.read().position_s == 1)
            manager = PlayerManager(Storage(tmp_path / 'config.json'))
            session = ActiveSession(Profile())
            session.track_source = source
            manager._active = session
            assert manager.track_position.title == 'FIFO title'
            await manager._teardown_session(session)
            assert manager.track_position is None and not source.running
            await manager._teardown_session(session)
            source.open()
            await asyncio.sleep(0.01)
            os.write(writer, item('minm', 'Next activation', 'core'))
            await until(lambda: source.read() is not None)
            assert source.read().title == 'Next activation'
        finally:
            await source.close()
            os.close(writer)
    asyncio.run(run())


def test_followers_expose_actual_target_without_changing_manual_behavior():
    manual = LmsFollower('host', 'AA:BB:CC:00:00:01', 'own')
    assert manual.target_mac == 'aa:bb:cc:00:00:01'
    auto = LmsSyncGroupObserver('host', 'own', lambda: ['own'], AsyncMock())
    assert auto.target_mac is None
    asyncio.run(auto._set_target('aa:bb:cc:00:00:02'))
    assert auto.target_mac == 'aa:bb:cc:00:00:02'


def test_delivery_anchor_rebased_without_changing_snapshot():
    with patch('huesync.track_position.time.monotonic', return_value=105):
        snapshot = TrackPosition(position_s=10, duration_s=100, playing=True, observed_at=100)
        assert snapshot.for_delivery()['position_s'] == 15
        assert snapshot.position_s == 10
        assert 'observed_at' not in snapshot.for_delivery()


def test_manager_selects_metadata_target_for_each_lms_mode(tmp_path):
    from huesync.models import VirtualPlayer

    async def run():
        manager = PlayerManager(Storage(tmp_path / 'config.json'))
        for mode, follow in [('manual', ''), ('manual', 'aa:bb:cc:00:00:01'), ('sync_group', '')]:
            player = VirtualPlayer(lms_host='host', follow_mode=mode, follow_player_mac=follow)
            session = ActiveSession(Profile(player_mac='02:00:00:00:00:01'))
            with patch.object(manager, '_start_squeezelite'), \
                 patch.object(manager, '_activate_lms_cava', new_callable=AsyncMock), \
                 patch.object(LmsTrackPositionSource, 'open'), \
                 patch.object(LmsSyncGroupObserver, 'start',
                              side_effect=lambda: asyncio.create_task(asyncio.sleep(100))):
                await manager._activate_lms(session, session.profile, player, None, None, [])
                source = session.track_source
                assert isinstance(source, LmsTrackPositionSource)
                assert source._target() == (None if mode == 'sync_group' else
                                            follow or session.profile.player_mac)
                if mode == 'sync_group':
                    # The observer owns the target; metadata does not derive a second one.
                    session.follower._follow_mac = 'aa:bb:cc:00:00:02'
                    assert source._target() == 'aa:bb:cc:00:00:02'
                await manager._teardown_session(session)
    asyncio.run(run())


def test_lms_activation_seeds_current_track_without_newsong(tmp_path):
    from huesync.lms_status import LmsPlayerStatus
    from huesync.models import Coupling, VirtualPlayer

    async def run():
        storage = Storage(tmp_path / 'config.json')
        player = VirtualPlayer(lms_host='host', follow_player_mac='aa:bb:cc:00:00:01')
        storage.save_virtual_player(player)
        manager = PlayerManager(storage)
        session = ActiveSession(Profile(lms_host='host', player_mac=player.player_mac),
                                coupling=Coupling(player_id=player.id))
        current = LmsPlayerStatus(title='Already playing', artist='Artist', time=83,
                                  duration=225, mode='play', player_name='Room')

        async def activate_audio(*args):
            session.poller_task = asyncio.create_task(manager._poll_sync_master(session))

        with patch.object(manager, '_start_squeezelite'), \
             patch.object(manager, '_activate_lms_cava', side_effect=activate_audio), \
             patch.object(manager, '_apply_probe_for_master', new_callable=AsyncMock), \
             patch.object(LmsTrackPositionSource, 'open'), \
             patch('huesync.player_manager.query_lms_status', return_value=current) as query:
            await manager._activate_lms(session, session.profile, player, None, None, [])
            manager._active = session
            await session.poller_task
            query.assert_called_once_with('host', player.follow_player_mac)
            snapshot = manager.track_position
            assert snapshot.title == 'Already playing'
            assert snapshot.artist == 'Artist'
            assert snapshot.position_s == 83 and snapshot.duration_s == 225
            assert snapshot.playing
            await manager.close()
    asyncio.run(run())


def test_airplay_stop_go_keeps_current_metadata_and_shutdown_closes_reader(tmp_path):
    from unittest.mock import MagicMock

    async def run():
        path = tmp_path / 'metadata'
        os.mkfifo(path, 0o600)
        source = AirPlayTrackPositionSource(str(path))
        manager = PlayerManager(Storage(tmp_path / 'config.json'))
        driver = MagicMock()
        driver.start = AsyncMock()
        driver.stop = AsyncMock()
        driver.aclose = AsyncMock()
        engine = MagicMock()
        engine.run = AsyncMock()
        with patch('huesync.player_manager.AirPlayTrackPositionSource', return_value=source), \
             patch('huesync.player_manager.AirPlayPipeStereoSource'), \
             patch('huesync.player_manager._make_canonical_pipeline'), \
             patch('huesync.player_manager.SyncEngine', return_value=engine), \
             patch('huesync.player_manager.HueDriver', return_value=driver), \
             patch.object(manager, '_configure_shairport_name', return_value=False):
            session = ActiveSession(Profile())
            await manager._activate_airplay(session, session.profile, None, None, [])
            manager._active = session
            await asyncio.sleep(0.01)
            writer = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(writer, item('minm', 'Current song', 'core') +
                         item('asar', 'Artist', 'core') + item('pbeg') +
                         item('prgr', '0/44100/441000'))
                await until(lambda: source.read() and source.read().duration_s == 10)
                await manager.deactivate()
                assert source.running and manager.track_position is None
                # Keep consuming events while stopped, without requiring a new title.
                os.write(writer, item('phbt', '88200/999'))
                await until(lambda: source.read().position_s == 2)
                next_session = ActiveSession(Profile())
                await manager._activate_airplay(next_session, next_session.profile, None, None, [])
                manager._active = next_session
                assert next_session.track_source is source
                assert manager.track_position.title == 'Current song'
                assert manager.track_position.artist == 'Artist'
                assert manager.track_position.position_s == 2
                os.write(writer, item('pend'))
                await until(lambda: source.read() is None)
                await manager.close()
                assert not source.running
                await manager.close()
            finally:
                await source.close()
                os.close(writer)
    asyncio.run(run())


def test_sync_group_target_query_seeds_track_on_activation(tmp_path):
    from huesync.lms_status import LmsPlayerStatus
    from huesync.models import VirtualPlayer

    async def run():
        manager = PlayerManager(Storage(tmp_path / 'config.json'))
        player = VirtualPlayer(lms_host='host', follow_mode='sync_group')
        session = ActiveSession(Profile(player_mac=player.player_mac))
        current = LmsPlayerStatus(title='Playing in group', artist='Artist', time=12,
                                  duration=90, mode='play')
        with patch.object(manager, '_start_squeezelite'), \
             patch.object(manager, '_activate_lms_cava', new_callable=AsyncMock), \
             patch.object(manager, '_apply_probe_for_master', new_callable=AsyncMock), \
             patch.object(LmsTrackPositionSource, 'open'), \
             patch.object(LmsSyncGroupObserver, 'start',
                          side_effect=lambda: asyncio.create_task(asyncio.sleep(100))), \
             patch('huesync.player_manager.query_lms_status', return_value=current) as query:
            await manager._activate_lms(session, session.profile, player, None, None, [])
            manager._active = session
            await session.follower._set_target('aa:bb:cc:00:00:01')
            query.assert_called_once_with('host', 'aa:bb:cc:00:00:01')
            assert manager.track_position.title == 'Playing in group'
            assert manager.track_position.position_s == 12
            await session.follower._set_target(None)
            assert manager.track_position is None
            await manager.close()
    asyncio.run(run())


def test_initial_lms_snapshot_survives_subscription_start():
    from huesync.lms_status import LmsPlayerStatus

    async def run():
        reader = asyncio.StreamReader()
        from unittest.mock import MagicMock
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        source = LmsTrackPositionSource('host', lambda: 'room')
        source.seed('room', LmsPlayerStatus(title='Current', mode='pause', time=3), 1.0)
        with patch('asyncio.open_connection', new_callable=AsyncMock,
                   return_value=(reader, writer)):
            source.open()
            await until(lambda: writer.write.called)
            assert source.read().title == 'Current'
            assert source.read().position_s == 3
            await source.close()
    asyncio.run(run())


def test_airplay_receiver_invalidation_discards_previous_track():
    source = AirPlayTrackPositionSource()
    source.feed(item('minm', 'Old receiver', 'core') + item('pbeg'))
    assert source.read().title == 'Old receiver'
    source.invalidate()
    assert source.read() is None
    source.feed(item('phbt', '44100/999'))
    assert source.read() is None
