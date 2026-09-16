# Installation

Target: Linux with Python 3.11+ and systemd. Commands below are target setup instructions,
not a claim that target runtime validation has passed. See [LXC deployment](deployment-lxc.md).

## Base package and native build

For Debian/Ubuntu, as root:

```sh
apt-get update
apt-get install -y git python3 python3-venv build-essential libfftw3-dev
cd /opt
git clone https://github.com/ThaYapeMan/HueSync.git huesync
cd /opt/huesync
python3 -m venv .venv
.venv/bin/pip install .
```

The Hatch build hook compiles `_libcavacore.so` and packages it in a platform wheel.
A source install requires GCC and FFTW headers even if you intend to select V2.
Native loading, wheel clean-install and allocation stress still require target checks.

## LMS routes

For external CAVA/FIFO, install the external `cava` program. For canonical LMS PCM,
build the pinned patched Squeezelite; a stock v0 visualizer is rejected.

```sh
apt-get install -y cava libasound2-dev libflac-dev libmad0-dev libmpg123-dev   libvorbis-dev libfaad-dev libopus-dev libssl-dev
cd /opt/huesync
bash scripts/build-squeezelite.sh
```

The script installs `/usr/local/bin/squeezelite`, automatically applies SHM v1 and
forces VISEXPORT. It does not install its build dependencies. See the
[producer guide](../squeezelite/README.md). Verify which binary the service actually
launches; installing a new binary does not upgrade a running old process.

For paced LMS playback, provision snd-dummy on the host and pass the audio devices
into the LXC. Do not substitute an unpaced null device without measuring behavior.

## AirPlay

Run `scripts/setup-airplay.sh` on the intended Linux target after reviewing its
service/dependency changes. It configures the shairport-sync PCM FIFO ingress.
Use `scripts/validate.sh` and logs to verify it; do not attach another reader to the
production FIFO while HueSync owns it.

## Service

The supplied unit uses user/group `huesync`, working directory `/opt/huesync`,
`/opt/huesync/.venv/bin/huesync` and `/etc/huesync/config.json`.
Create the account and writable config directory before enabling it:

```sh
id huesync >/dev/null 2>&1 || useradd --system --home /opt/huesync --shell /usr/sbin/nologin huesync
install -d -o huesync -g huesync /etc/huesync
cp /opt/huesync/systemd/huesync.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now huesync
```

Grant required audio-device access for the selected ingress according to the target's
group/device mapping. Review the unit's wildcard SHM cleanup and its user permissions: it is intended for a
dedicated HueSync host/container, not unrelated visualizer users.
Open `http://<host>:8420` on a trusted network. Configure controller credentials and
an Entertainment zone through the UI/API. These installation instructions do not
add an authentication/reverse-proxy policy.

## Updates

From the existing target checkout, as root: `bash scripts/update.sh`. It fast-forward
pulls, installs HueSync native prerequisites, installs Python dependencies and restarts
the service. It does **not** rebuild Squeezelite or provision all producer dependencies.
Rebuild the producer deliberately when its pinned revision/patch changes.
