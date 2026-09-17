"""Advertisement-only patch checks; live Avahi/iOS behavior needs the target LXC."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Verbatim relevant excerpt from pinned Shairport 0b1c4391 rtsp.c.
BEFORE = '''    snprintf(firmware_version, sizeof(firmware_version), "fv=%s", PACKAGE_VERSION);

#ifdef CONFIG_AIRPLAY_2
  uint64_t features_hi = config.airplay_features;
  features_hi = (features_hi >> 32) & 0xffffffff;
  uint64_t features_lo = config.airplay_features;
  features_lo = features_lo & 0xffffffff;
  snprintf(ap1_featuresString, sizeof(ap1_featuresString), "ft=0x%" PRIX64 ",0x%" PRIX64 "",
           features_lo, features_hi);
  snprintf(pkString, sizeof(pkString), "pk=");
'''


def apply(source):
    script = (ROOT / 'scripts/setup-airplay.sh').read_text()
    function = script.split('apply_feature_mask_experiment() {', 1)[1].split('\n}', 1)[0]
    return subprocess.run(['bash', '-c', 'set -Eeuo pipefail\nSCRIPT_DIR="$1"\n'
                           'apply_feature_mask_experiment() {' + function + '\n}\n'
                           'apply_feature_mask_experiment "$2"', 'test',
                           str(ROOT / 'scripts'), str(source)], capture_output=True, text=True)


def test_real_patch_application_idempotency_and_advertised_strings(tmp_path):
    source = tmp_path / 'rtsp.c'
    source.write_text(BEFORE)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    assert apply(tmp_path).returncode == 0
    patched = source.read_text()
    assert apply(tmp_path).returncode == 0
    assert source.read_text() == patched
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('C compiler required for advertisement formatting check')
    declarations = '\n'.join(re.findall(r'  uint64_t features_.*;', patched))
    raop = re.search(r'  snprintf\(ap1_featuresString,.*?features_hi\);', patched, re.S).group()
    airplay = raop.replace('ap1_featuresString', 'featuresString').replace('ft=', 'features=')
    program = tmp_path / 'format.c'
    program.write_text('#include <inttypes.h>\n#include <stdio.h>\nint main(void) {\n'
                       'char ap1_featuresString[80], featuresString[80];\n' + declarations
                       + '\n' + raop + '\n'
                       + airplay
                       + '\nputs(featuresString); puts(ap1_featuresString);\n}\n')
    executable = tmp_path / 'format'
    subprocess.run([compiler, '-Wall', '-Werror', str(program), '-o', str(executable)], check=True)
    assert subprocess.check_output([str(executable)], text=True).splitlines() == [
        'features=0x445F8A00,0x801C340', 'ft=0x445F8A00,0x801C340']


def test_unexpected_source_fails_instead_of_silently_building(tmp_path):
    source = tmp_path / 'rtsp.c'
    original = BEFORE.replace('config.airplay_features', 'unexpected_source')
    source.write_text(original)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    assert apply(tmp_path).returncode != 0
    assert source.read_text() == original


def test_build_preserves_pinned_firmware_advertisement(tmp_path):
    # Execute the actual header generation, substituting only the git executable.
    script = (ROOT / 'scripts/setup-airplay.sh').read_text()
    command = script.split('# Keep advertised fv unchanged:', 1)[1].split('make -j', 1)[0]
    command = command[command.index('printf'):]
    shell = ('set -Eeuo pipefail\nSHAIRPORT_COMMIT=pinned\n'
             'git() { [[ "$*" == "describe --tags pinned" ]]; echo 4.3.7; }\n' + command)
    subprocess.run(['bash', '-c', shell], cwd=tmp_path, check=True)
    assert (tmp_path / 'gitversion.h').read_text() == 'char git_version_string[] = "4.3.7";\n'
