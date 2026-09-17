"""Deletion safety through real API/storage and explicit offline repair."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from huesync.api import router
from huesync.backup import configuration_lease
from huesync.migration import convert, inspect_coupling, migrate_file
from huesync.player_manager import PlayerManager
from huesync.schema import validate_current
from huesync.storage import ReferencedEntityError, Storage

FIXTURE = Path(__file__).parent / 'fixtures/a72893b-config.json'


@pytest.fixture()
def configured(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(convert(json.loads(FIXTURE.read_bytes()))))
    return Storage(path)


@pytest.mark.parametrize('endpoint,identity,blocker', [
    ('virtual-players', 'player-1', 'coupling-1'),
    ('zones', 'zone-1', 'coupling-1'),
    ('analysers', 'analyser-1', 'coupling-1'),
    ('energy-profiles', 'energy-1', 'coupling-1'),
    ('controllers', 'bridge-1', 'zone-1'),
    ('effects', 'effect-1', 'energy-1'),
    ('effects', 'effect-4', 'energy-1'),  # low-energy reference, not only high
])
def test_api_delete_referenced_entity_is_atomic(configured, endpoint, identity, blocker):
    app = FastAPI()
    app.include_router(router)
    app.state.storage = configured
    app.state.player_manager = PlayerManager(configured)
    original = configured.path.read_bytes()
    with TestClient(app) as client, patch.object(configured, '_write') as write:
        response = client.delete(f'/api/{endpoint}/{identity}')
    assert response.status_code == 409
    assert blocker in response.json()['detail']
    assert 'Reassign or remove' in response.json()['detail']
    if endpoint == 'virtual-players':
        for coupling in configured.list_couplings():
            if coupling.player_id == identity:
                assert coupling.id in response.json()['detail']
                assert coupling.name in response.json()['detail']
    write.assert_not_called()
    assert configured.path.read_bytes() == original
    validate_current(configured.read_configuration(), references=True)


def test_storage_guards_and_explicit_unbinding_allow_delete(configured):
    with pytest.raises(ReferencedEntityError):
        configured.delete_virtual_player('player-1')
    for coupling in configured.list_couplings():
        if coupling.player_id == 'player-1':
            configured.delete_coupling(coupling.id)
    configured.delete_virtual_player('player-1')
    assert configured.get_virtual_player('player-1') is None
    validate_current(configured.read_configuration(), references=True)


@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('replacement', [None, 'player-2'])
def test_explicit_dangling_repair_preserves_backup_and_validates(tmp_path, historical, replacement):
    data = json.loads(FIXTURE.read_bytes())
    if not historical:
        data = convert(data)
    # Give just the identified binding a deleted identity, leaving other bindings intact.
    data['couplings'][0]['player_id'] = 'intentionally-deleted-player'
    data['active_coupling_id'] = 'coupling-1'
    path = tmp_path / 'config.json'
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    report = inspect_coupling(path, 'coupling-1')
    assert report['missing_player']
    assert report['coupling']['name'] == data['couplings'][0]['name']
    assert 'SYNTHETIC-APP-KEY' not in json.dumps(report)
    assert path.read_bytes() == raw
    args = dict(repair=('coupling-1', replacement), expected_sha256=report['sha256'])
    assert migrate_file(path, check=True, **args)
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob('*.bak'))
    with patch('huesync.migration.os.replace', side_effect=OSError('disk failure')):
        with pytest.raises(OSError):
            migrate_file(path, **args)
    assert path.read_bytes() == raw
    assert next(tmp_path.glob('*.bak')).read_bytes() == raw
    assert migrate_file(path, **args)
    current = json.loads(path.read_bytes())
    validate_current(current, references=True)
    assert current['active_coupling_id'] is None
    repaired = [c for c in current['couplings'] if c['id'] == 'coupling-1']
    assert ([c['player_id'] for c in repaired] == [replacement]) if replacement else not repaired
    assert current['controllers'] == data['controllers']
    assert not migrate_file(path)


def test_repair_requires_identification_and_valid_replacement(configured):
    path = configured.path
    data = json.loads(path.read_bytes())
    data['couplings'][0]['player_id'] = 'missing'
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='fingerprint'):
        migrate_file(path, repair=('coupling-1', None))
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='Replacement player'):
        migrate_file(path, repair=('coupling-1', 'missing-too'), expected_sha256=digest)
    assert path.read_bytes() == raw
    assert not list(path.parent.glob('*.bak'))


@pytest.mark.parametrize('endpoint,field', [
    ('zones', 'controller_id'), ('energy-profiles', 'high_energy_effect_id'),
    ('energy-profiles', 'low_energy_effect_id'), ('couplings', 'player_id'),
    ('couplings', 'zone_id'), ('couplings', 'analyser_id'), ('couplings', 'energy_profile_id'),
])
def test_api_rejects_new_dangling_references(configured, endpoint, field):
    app = FastAPI()
    app.include_router(router)
    app.state.storage = configured
    original = configured.path.read_bytes()
    with TestClient(app) as client:
        for method, path in [('post', endpoint), ('patch', endpoint + '/existing')]:
            response = getattr(client, method)('/api/' + path, json={field: 'missing'})
            assert response.status_code == 409
            assert field in response.json()['detail']
    assert configured.path.read_bytes() == original


def test_repair_cli_inspection_fingerprint_and_runtime_exclusion(configured):
    path = configured.path
    data = json.loads(path.read_bytes())
    data['couplings'][0]['player_id'] = 'missing'
    path.write_text(json.dumps(data))
    raw = path.read_bytes()
    command = [sys.executable, '-B', '-m', 'huesync.migration', str(path)]
    env = dict(os.environ, PYTHONPATH=str(FIXTURE.parents[2] / 'src'))
    report = subprocess.run(command + ['--inspect-coupling', 'coupling-1'],
                            env=env, capture_output=True, text=True, check=True)
    digest = json.loads(report.stdout)['sha256']
    assert path.read_bytes() == raw
    assert not list(path.parent.glob('*.bak'))
    repair = command + ['--remove-dangling-coupling', 'coupling-1', '--expect-sha256', digest]
    with configuration_lease(path):
        blocked = subprocess.run(repair, env=env, capture_output=True, text=True)
        assert blocked.returncode != 0
        assert 'Configuration is in use' in blocked.stderr
    assert path.read_bytes() == raw
    success = subprocess.run(repair, env=env, capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    validate_current(json.loads(path.read_bytes()), references=True)
    assert next(path.parent.glob('*.bak')).read_bytes() == raw


def test_clone_cannot_duplicate_an_already_dangling_binding(configured):
    data = configured.read_configuration()
    data['couplings'][0]['player_id'] = 'missing'
    configured.path.write_text(json.dumps(data))
    before = configured.path.read_bytes()
    app = FastAPI()
    app.include_router(router)
    app.state.storage = configured
    with TestClient(app) as client:
        response = client.post('/api/couplings/coupling-1/clone')
    assert response.status_code == 409
    assert configured.path.read_bytes() == before
