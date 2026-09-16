"""Installer boundaries tested without pretending to run apt/systemd in mocks."""
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/install-huesync.sh'


def test_help_and_unsupported_platform_are_read_only(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run([str(SCRIPT), '--help'], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and '--check' in result.stdout
    for requirement in ('existing HueSync target installation', 'Debian 13 (trixie)',
                        'x86_64', 'systemd running', '/run/systemd/system',
                        'Does not install, migrate or modify the host', 'docs/testing.md'):
        assert requirement in result.stdout
    assert not list(tmp_path.iterdir())
    release = Path('/etc/os-release').read_text()
    if 'ID=debian' in release and 'VERSION_ID="13"' in release:
        pytest.skip('This failure test runs only on unsupported review hosts')
    before = subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT)
    result = subprocess.run([str(SCRIPT), '--check'], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and 'Supported target: Debian 13' in result.stderr
    assert subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT) == before
    assert not list(tmp_path.iterdir())


def test_shell_syntax():
    # Windows-mounted checkouts can appear executable despite Git's stored mode.
    entry = subprocess.check_output(
        ['git', 'ls-files', '-s', 'scripts/install-huesync.sh'], cwd=ROOT, text=True)
    assert entry.startswith('100755 '), 'Fresh clones must support the executable installer'
    subprocess.run(['bash', '-n', str(SCRIPT), str(ROOT / 'scripts/setup-airplay.sh'),
                    str(ROOT / 'scripts/build-squeezelite.sh')], check=True)


def test_dependency_and_phase_contract():
    text = SCRIPT.read_text()
    for package in ('libasound2-dev', 'libfftw3-dev', 'libflac-dev', 'libmad0-dev',
                    'libmpg123-dev', 'libvorbis-dev', 'libfaad-dev', 'libcap2-bin',
                    'python3-venv', 'pkg-config', 'polkitd', 'avahi-daemon', 'cava'):
        assert package in text
    assert 'npm ci' in text
    assert 'sha256sum --check' in text
    assert 'git archive' not in text  # invocation includes explicit repo/safety options
    assert 'archive HEAD' in text
    assert text.index('-m huesync.migration') < text.index('systemctl restart avahi-daemon')
    assert text.index('verify "$RELEASE/venv"') < text.index('systemctl restart avahi-daemon')
    assert 'pip install -e' not in text
    assert 'HUESYNC_DEFER_START=1' in text


def test_check_branch_never_installs_or_migrates():
    text = SCRIPT.read_text()
    check = text.split('if [[ "$CHECK" == 1 ]]; then', 1)[1].split('\nfi', 1)[0]
    assert 'verify "$PREFIX/.venv"' in check
    assert 'exit 0' in check
    for forbidden in ('apt-get', 'mkdir', 'chown', 'systemctl restart', '-m huesync.migration'):
        assert forbidden not in check
    verification = (ROOT / 'scripts/verify-install.py').read_text()
    assert 'check=True' in verification


def load_hook(monkeypatch):
    # Only the Hatch base class is substituted. Run real generation and cleanup;
    # native compilation gets separate compiler tests / target build evidence.
    module = types.ModuleType('hatchling.builders.hooks.plugin.interface')
    module.BuildHookInterface = object
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = importlib.util.spec_from_file_location('installer_build_hook', ROOT / 'hatch_build.py')
    hook_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_module)
    return hook_module.CustomBuildHook


def test_build_commit_generated_without_source_mutation(tmp_path, monkeypatch):
    hook_type = load_hook(monkeypatch)
    hook = hook_type()
    hook.root = str(tmp_path)
    tracked = tmp_path / 'src/huesync/_commit.py'
    tracked.parent.mkdir(parents=True)
    tracked.write_text('COMMIT = "stale"\n')
    monkeypatch.setenv('HUESYNC_BUILD_COMMIT', '123456789abcdef')
    monkeypatch.setattr(hook, '_build_cavacore', lambda: Path(
        hook._generated.name, '_libcavacore.so').write_bytes(b'test boundary'))
    data = {}
    hook.initialize('standard', data)
    generated = {dest: Path(src) for src, dest in data['force_include'].items()}
    assert generated['huesync/_commit.py'].read_text() == 'COMMIT = "123456789abcdef"\n'
    assert generated['huesync/cavacore/_libcavacore.so'].exists()
    assert tracked.read_text() == 'COMMIT = "stale"\n'
    assert data['pure_python'] is False and data['infer_tag'] is True
    hook.finalize('standard', data, '')
    assert not generated['huesync/_commit.py'].exists()


def test_metadata_rejects_injected_non_revision(tmp_path, monkeypatch):
    hook = load_hook(monkeypatch)()
    hook.root = str(tmp_path)
    monkeypatch.setenv('HUESYNC_BUILD_COMMIT', 'bad";code')
    with pytest.raises(ValueError, match='hexadecimal'):
        hook.initialize('standard', {})


def test_every_service_must_be_active():
    text = SCRIPT.read_text()
    function = text.split('verify_services() {', 1)[1].split('\n}', 1)[0]
    # Model systemctl's real any-active exit convention: a single failed unit
    # must be caught even though HueSync and the other services are active.
    shell = '''fail() { echo "$*"; exit 1; }
systemctl() { [[ "$*" != *shairport-sync* ]]; }
verify_services() {'''+function+'\n}\nverify_services\n'
    result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Service is not active: shairport-sync' in result.stdout


def test_airplay_first_install_replaces_upstream_sample_only(tmp_path):
    text = (ROOT / 'scripts/setup-airplay.sh').read_text()
    detect = text.split('CONFIG_EXISTED=0', 1)[1].split('\n\n', 1)[0]
    write = text.split('if [[ "$CONFIG_EXISTED" == 0 ]]; then', 1)[1].split('\nfi', 1)[0]
    config = tmp_path / 'receiver.conf'
    for existing in (False, True):
        if existing:
            config.write_text('operator config')
        shell = ('CONFIG_EXISTED=0'+detect+'\n'
                 f'if [[ ! -f "{config}" ]]; then echo upstream-sample > "{config}"; fi\n'
                 'if [[ "$CONFIG_EXISTED" == 0 ]]; then'+write+'\nfi\n')
        shell = shell.replace('/usr/local/etc/shairport-sync.conf', str(config))
        subprocess.run(['bash', '-c', shell], check=True)
        if existing:
            assert config.read_text() == 'operator config'
        else:
            assert 'output_backend = "pipe"' in config.read_text()
            assert 'output_rate = 44100' in config.read_text()


@pytest.mark.parametrize('debian,arch,booted,success,message', [
    (True, 'x86_64', True, True, ''),
    (True, 'x86_64', False, False, 'booted systemd target is required'),
    (True, 'aarch64', True, False, 'x86_64 only'),
    (False, 'x86_64', True, False, 'Debian 13 (trixie) only'),
])
def test_platform_gate_read_only(tmp_path, debian, arch, booted, success, message):
    # Execute the real probe body with only OS identity paths substituted.
    # This proves the gate; it does not simulate an installed target.
    release = tmp_path / 'os-release'
    release.write_text('ID=debian\nVERSION_ID=13\nVERSION_CODENAME=trixie\n' if debian
                       else 'ID=ubuntu\nVERSION_ID=26.04\n')
    systemd = tmp_path / 'systemd'
    if booted:
        systemd.mkdir()
    function = SCRIPT.read_text().split('platform() {', 1)[1].split('\n}', 1)[0]
    function = function.replace('/etc/os-release', str(release)).replace(
        '/run/systemd/system', str(systemd))
    before = sorted(p.name for p in tmp_path.iterdir())
    shell = ('set -Eeuo pipefail\nfail() { echo "$*" >&2; exit 1; }\n'
             f'uname() {{ echo {arch}; }}\nplatform() {{'+function+'\n}\nplatform\n')
    result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
    assert (result.returncode == 0) == success
    assert message in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == before
