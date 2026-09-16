"""Explicit migration boundary: history is never accepted by current runtime."""
import copy
import json
from unittest.mock import patch

import pytest

from huesync.migration import convert, migrate_file
from huesync.models import Coupling, Effect, EnergyProfile
from huesync.schema import empty_config, validate_current
from huesync.storage import Storage


def historical():
    return {
        'controllers': [{'id': 'ctrl'}], 'virtual_players': [{'id': 'player'}],
        'light_providers': [{'id': 'zone', 'controller_id': 'ctrl'}],
        'analysis_configs': [{'id': 'analysis'}],
        'scenes': [{'id': 'bright', 'effect_type': 'spectrum_rgb'},
                   {'id': 'quiet', 'effect_type': 'wave'}],
        'crossfaders': [{'id': 'energy', 'active_scene_id': 'bright',
                        'mellow_scene_id': 'quiet', 'low_threshold': .2}],
        'couplings': [{'id': 'run', 'player_id': 'player', 'light_provider_id': 'zone',
                       'analysis_config_id': 'analysis', 'crossfader_id': 'energy'}],
        'active_coupling_id': 'run',
    }


def test_migration_backup_references_and_idempotency(tmp_path):
    path = tmp_path / 'config.json'
    original = json.dumps(historical()).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError):
        Storage(path)
    assert migrate_file(path, check=True)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob('*.bak'))
    assert migrate_file(path)
    backups = list(tmp_path.glob('*.bak'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    current = json.loads(path.read_bytes())
    validate_current(current, references=True)
    assert current['couplings'][0] == dict(id='run', player_id='player', zone_id='zone',
                                         analyser_id='analysis', energy_profile_id='energy')
    assert current['energy_profiles'][0] == dict(id='energy', high_energy_effect_id='bright',
                                               low_energy_effect_id='quiet', blend_start=.2)
    assert {x['id'] for x in current['effects']} == {'bright', 'quiet'}
    before = path.stat().st_mtime_ns
    assert not migrate_file(path)
    assert path.stat().st_mtime_ns == before
    assert len(list(tmp_path.glob('*.bak'))) == 1
    assert Storage(path).get_energy_profile('energy').low_energy_effect_id == 'quiet'
    for forbidden in ('crossfaders', 'light_provider_id', 'analysis_config_id', 'crossfader_id'):
        assert forbidden not in path.read_text()


@pytest.mark.parametrize('old,new', [('light_provider_id', 'zone_id'),
                                   ('analysis_config_id', 'analyser_id'),
                                   ('crossfader_id', 'energy_profile_id')])
def test_conflicting_keys_fail_without_rewrite(tmp_path, old, new):
    data = historical()
    data['couplings'][0][new] = 'different'
    path = tmp_path / 'config.json'
    raw = json.dumps(data)
    path.write_text(raw)
    with pytest.raises(ValueError, match='Conflicting'):
        migrate_file(path)
    assert path.read_text() == raw
    assert not list(tmp_path.glob('*.bak'))


def test_collection_conflict_and_equal_alias():
    data = historical()
    data['energy_profiles'] = []
    with pytest.raises(ValueError, match='Conflicting'):
        convert(data)
    data['energy_profiles'] = copy.deepcopy(data['crossfaders'])
    assert 'crossfaders' not in convert(data)


@pytest.mark.parametrize('broken', [None, [], {'schema_version': 2},
                                     {'couplings': 'bad'}, {'unknown': 'preserve me'}])
def test_malformed_or_unknown_fails_safely(tmp_path, broken):
    path = tmp_path / 'config.json'
    raw = json.dumps(broken)
    path.write_text(raw)
    with pytest.raises((ValueError, TypeError)):
        migrate_file(path)
    assert path.read_text() == raw


def test_dangling_reference_fails():
    data = historical()
    data['crossfaders'][0]['active_scene_id'] = 'missing'
    with pytest.raises(ValueError, match='dangling'):
        convert(data)


def test_atomic_replace_failure_preserves_original_and_backup(tmp_path):
    path = tmp_path / 'config.json'
    raw = json.dumps(historical()).encode()
    path.write_bytes(raw)
    with patch('huesync.migration.os.replace', side_effect=OSError('disk')):
        with pytest.raises(OSError):
            migrate_file(path)
    assert path.read_bytes() == raw
    assert next(tmp_path.glob('*.bak')).read_bytes() == raw


@pytest.mark.parametrize('old', ['light_provider_id', 'analysis_config_id', 'crossfader_id'])
def test_current_coupling_rejects_history(old):
    with pytest.raises(TypeError):
        Coupling.from_dict({old: 'x'})


def test_current_models_and_storage_reject_history(tmp_path):
    for cls, old in [(EnergyProfile, 'active_scene_id'), (Effect, 'color_mode')]:
        with pytest.raises(TypeError):
            cls.from_dict({old: 'x'})
    data = empty_config()
    data['crossfaders'] = []
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Storage(path)


def test_current_and_new_install_no_migration(tmp_path):
    data = empty_config()
    assert convert(data) == data
    path = tmp_path / 'config.json'
    assert migrate_file(path)
    assert not migrate_file(path)
    assert not list(tmp_path.glob('*.bak'))


def test_bound_effect_settings_cannot_be_silently_discarded(tmp_path):
    data = historical()
    data['scenes'][0]['mix_low_threshold'] = .9
    path = tmp_path / 'config.json'
    original = json.dumps(data)
    path.write_text(original)
    with pytest.raises(ValueError, match='refusing data loss'):
        migrate_file(path)
    assert path.read_text() == original
    assert not list(tmp_path.glob('*.bak'))
