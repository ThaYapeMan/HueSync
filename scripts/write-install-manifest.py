"""Record installed binary provenance after successful builds, before activation."""
import hashlib
import json
import sys
from pathlib import Path

commit, short, release = sys.argv[1:]
binary = Path('/usr/local/bin/squeezelite')
manifest = {
    'commit': commit, 'short_commit': short,
    'squeezelite_revision': 'c7c4248ddd70e47dbfeba0bf4a8a7ec08d8a995c',
    'squeezelite_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'fifo_producer_sha256': hashlib.sha256(
        Path('/usr/local/bin/huesync-squeezelite-fifo').read_bytes()).hexdigest(),
    'producer_objects': ['output_vis.o', 'output_vis_v1.o'],
}
Path(release, 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
