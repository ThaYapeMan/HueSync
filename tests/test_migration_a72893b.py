"""Synthetic a72893b persisted residue, never real Controller credentials.

The fixture has the exact reported top-level keys/counts. BridgeConfig/Profile
fields were reconstructed from a72893b models.py. Storage at that revision kept
but did not read these collections; 15e4b66 mapped their IDs to Controller/Coupling.
"""
import copy
import json
import os
import shutil
import subprocess
import venv
from pathlib import Path
from unittest.mock import patch

import numpy
import pytest
import soxr

from huesync.migration import convert, migrate_file
from huesync.schema import SCHEMA_VERSION, empty_config, validate_current
from huesync.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / 'fixtures/a72893b-config.json'


def historical():
    return json.loads(FIXTURE.read_text())


def test_exact_a72893b_upgrade_preserves_every_current_field():
    old = historical()
    counts = dict(analysers=5, bridges=1, controllers=1, couplings=7,
                  crossfaders=3, player_latencies=2, profiles=2, scenes=4,
                  virtual_players=2, zones=1)
    assert set(old) == set(counts) | {'active_coupling_id', 'active_profile_id'}
    assert {key: len(old[key]) for key in counts} == counts
    assert old['active_coupling_id'] is old['active_profile_id'] is None
    unchanged = copy.deepcopy(old)
    current = convert(old)
    assert old == unchanged
    assert set(current) == set(empty_config())
    assert current['schema_version'] == SCHEMA_VERSION
    for key in ('controllers', 'virtual_players', 'zones', 'analysers',
                'couplings', 'player_latencies', 'active_coupling_id'):
        assert current[key] == old[key]
    assert current['effects'] == old['scenes']
    assert current['energy_profiles'] == old['crossfaders']
    validate_current(current, references=True)
    assert convert(current) == current


def test_unique_bridge_and_profile_are_converted_not_discarded():
    old = historical()
    old['bridges'][0]['id'] = 'unique-bridge'
    old['bridges'][0]['app_key'] = 'UNIQUE-SYNTHETIC-KEY'
    old['profiles'][0].update(id='unique-profile', bridge_id='unique-bridge', name='Unique')
    result = convert(old)
    for key in ('controllers', 'virtual_players', 'zones', 'analysers', 'couplings'):
        assert all(row in result[key] for row in old[key])
    bridge = next(r for r in result['controllers'] if r['id'] == 'unique-bridge')
    assert bridge == dict(old['bridges'][0], type='hue')
    coupling = next(r for r in result['couplings'] if r['id'] == 'unique-profile')
    assert coupling['name'] == 'Unique'
    profile = old['profiles'][0]
    used = {'id', 'name', 'enabled'}
    for collection, ref in (('virtual_players', 'player_id'), ('zones', 'zone_id'),
                            ('analysers', 'analyser_id'), ('energy_profiles', 'energy_profile_id')):
        entity = next(r for r in result[collection] if r['id'] == coupling[ref])
        if collection == 'energy_profiles':
            assert entity['low_energy_effect_id'] == ''
            effect = next(r for r in result['effects']
                          if r['id'] == entity['high_energy_effect_id'])
            for field in profile.keys() & effect.keys() - {'id', 'name'}:
                assert effect[field] == profile[field]
                used.add(field)
        for field in profile.keys() & entity.keys() - {'id', 'name'}:
            assert entity[field] == profile[field]
            used.add(field)
        if collection == 'zones':
            assert entity['controller_id'] == profile['bridge_id']
            used.add('bridge_id')
    assert used == set(profile), 'Every historical Profile field must have a current owner'
    assert coupling['enabled'] == profile['enabled']
    assert result['active_coupling_id'] is None
    assert convert(old) == result  # generated IDs deterministic
    validate_current(result, references=True)


@pytest.mark.parametrize('collection,field,value', [
    ('bridges', 'app_key', 'CONFLICTING-SECRET'),
    ('bridges', 'client_key', 'CONFLICTING-SECRET'),
    ('bridges', 'host', '192.0.2.99'),
    ('profiles', 'lower_cutoff_freq', 999),
])
def test_conflicting_residue_fails_without_writing_or_secret_disclosure(
        tmp_path, capsys, collection, field, value):
    data = historical()
    data[collection][0][field] = value
    path = tmp_path / 'config.json'
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='Conflicting historical') as error:
        migrate_file(path)
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob('*.bak'))
    output = str(error.value) + capsys.readouterr().out
    assert 'CONFLICTING-SECRET' not in output
    assert 'SYNTHETIC-APP-KEY' not in output


@pytest.mark.parametrize('collection', ['bridges', 'profiles'])
@pytest.mark.parametrize('defect', ['not-list', 'not-row', 'duplicate', 'missing-id',
                                    'unknown-field', 'invalid-type'])
def test_malformed_residue_fails_closed(tmp_path, collection, defect):
    data = historical()
    if defect == 'not-list':
        data[collection] = {}
    elif defect == 'not-row':
        data[collection] = [None]
    elif defect == 'duplicate':
        data[collection].append(copy.deepcopy(data[collection][0]))
    elif defect == 'missing-id':
        del data[collection][0]['id']
    elif defect == 'unknown-field':
        data[collection][0]['unknown_setting'] = 'must-not-disappear'
    else:
        data[collection][0]['name'] = 123
    path = tmp_path / 'config.json'
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises((ValueError, TypeError)):
        migrate_file(path)
    assert path.read_bytes() == raw


def test_aliases_conflict_checks_and_active_coupling_preserved():
    data = historical()
    data['active_coupling_id'] = 'coupling-3'
    data['profiles'][0]['color_mode'] = data['profiles'][0]['effect_type']
    result = convert(data)
    assert result['active_coupling_id'] == 'coupling-3'
    data['profiles'][0]['color_mode'] = 'none'
    with pytest.raises(ValueError, match='Conflicting'):
        convert(data)


def test_exact_bytes_backup_failure_and_idempotency(tmp_path):
    path = tmp_path / 'config.json'
    raw = FIXTURE.read_bytes()
    path.write_bytes(raw)
    path.chmod(0o600)
    assert migrate_file(path, check=True)
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob('*.bak'))
    with patch('huesync.migration.os.replace', side_effect=OSError('disk unavailable')):
        with pytest.raises(OSError):
            migrate_file(path)
    assert path.read_bytes() == raw
    backup = next(tmp_path.glob('*.bak'))
    assert backup.read_bytes() == raw
    assert backup.stat().st_mode & 0o777 == 0o600
    assert migrate_file(path)
    before = path.stat().st_mtime_ns
    assert not migrate_file(path)
    assert path.stat().st_mtime_ns == before
    assert list(tmp_path.glob('*.bak')) == [backup]
    assert backup.read_bytes() == raw
    validate_current(json.loads(path.read_bytes()), references=True)


@pytest.mark.parametrize('key', ['bridges', 'profiles', 'scenes', 'crossfaders',
                                 'active_profile_id'])
def test_current_runtime_still_rejects_historical_keys(tmp_path, key):
    data = empty_config()
    data[key] = None if key == 'active_profile_id' else []
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='persisted collections'):
        Storage(path)


@pytest.mark.parametrize("edited_coupling", [False, True])
def test_installer_migration_command_in_isolated_environment(tmp_path, edited_coupling):
    # Same isolated Python/module command as installer phase 4, without apt/services.
    installer = (ROOT / 'scripts/install-huesync.sh').read_text()
    assert '"$RELEASE/venv/bin/python" -I -B -m huesync.migration "$CONFIG"' in installer
    environment = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    python = environment / 'bin/python'
    site = subprocess.check_output(
        [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
        text=True).strip()
    # Reuse real declared runtime dependencies, including on hosts where they
    # are user-installed (-I excludes user site); no dependency/native mocks.
    roots = {str(Path(module.__file__).resolve().parents[1]) for module in (numpy, soxr)}
    (Path(site) / 'test-runtime-dependencies.pth').write_text('\n'.join(sorted(roots)) + '\n')
    shutil.copytree(ROOT / 'src/huesync', Path(site) / 'huesync',
                    ignore=shutil.ignore_patterns('__pycache__', 'webui'))
    config = tmp_path / 'config.json'
    data = historical()
    if edited_coupling:
        data['couplings'][0].update(name='Renamed in current UI', enabled=False)
    original = json.dumps(data).encode()
    config.write_bytes(original)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    command = [str(python), '-I', '-B', '-m', 'huesync.migration', str(config)]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert 'Schema 1: valid' in result.stdout
    assert subprocess.run(command + ['--check'], capture_output=True).returncode == 0
    assert next(tmp_path.glob('*.bak')).read_bytes() == original
    assert json.loads(config.read_bytes())['couplings'] == data['couplings']
    assert Storage(config).get_controller('bridge-1').app_key == 'SYNTHETIC-APP-KEY'


def test_unique_profile_with_dangling_bridge_fails(tmp_path):
    data = historical()
    data['profiles'][0].update(id='unique', bridge_id='missing')
    path = tmp_path / 'config.json'
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='dangling'):
        migrate_file(path)
    assert path.read_bytes() == raw


def test_generated_identity_collision_does_not_overwrite_current_entities():
    data = historical()
    data['profiles'][0]['id'] = 'unique'
    candidate = convert(data)
    generated = next(c for c in candidate['couplings'] if c['id'] == 'unique')
    clash = copy.deepcopy(next(a for a in candidate['analysers']
                               if a['id'] == generated['analyser_id']))
    clash['onset_delta'] = .75
    data['analysers'].append(clash)
    before = copy.deepcopy(data)
    with pytest.raises(ValueError, match='Conflicting historical/current analysers'):
        convert(data)
    assert data == before


@pytest.mark.parametrize('metadata', [
    {'name': 'Renamed via Coupling API'}, {'enabled': False},
    {'name': 'Renamed via Coupling API', 'enabled': False},
])
def test_retained_profile_does_not_override_current_coupling_metadata(tmp_path, metadata):
    # a72893b PATCH saves Coupling metadata, never updates retained profiles.
    data = historical()
    data['couplings'][0].update(metadata)
    path = tmp_path / 'config.json'
    original = json.dumps(data).encode()
    path.write_bytes(original)
    assert migrate_file(path)
    result = json.loads(path.read_bytes())
    for key in ('controllers', 'virtual_players', 'zones', 'analysers',
                'couplings', 'player_latencies', 'active_coupling_id'):
        assert result[key] == data[key]
    assert result['effects'] == data['scenes']
    assert result['energy_profiles'] == data['crossfaders']
    assert set(result) == set(empty_config())
    validate_current(result, references=True)
    assert next(tmp_path.glob('*.bak')).read_bytes() == original
    assert not migrate_file(path)


@pytest.mark.parametrize('collection,field,value', [
    ('virtual_players', 'lms_host', '192.0.2.99'),
    ('zones', 'entertainment_area_id', 'another-area'),
    ('analysers', 'onset_delta', .99),
    ('scenes', 'effect_type', 'wave'),
    ('crossfaders', 'blend_start', .1),
])
def test_metadata_precedence_does_not_hide_binding_conflicts(tmp_path, collection, field, value):
    data = historical()
    data['couplings'][0].update(name='Renamed', enabled=False)
    data[collection][0][field] = value
    path = tmp_path / 'config.json'
    original = json.dumps(data).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match='Conflicting historical/current profiles'):
        migrate_file(path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('*.bak'))
