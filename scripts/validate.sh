#!/usr/bin/env bash
# Post-install/deployment validation for HueSync.
#
# Checks Python environment, services, Shairport PCM contract, runtime
# directory ownership, and FIFO lifecycle — without consuming PCM.
#
# SAFETY: This script NEVER reads or opens /run/huesync/airplay.pcm.
# FIFO presence and type are checked via stat; open file descriptors are
# inspected via lsof (which inspects kernel FD tables, not FIFO content).
#
# Usage (on the LXC / target machine, as root or huesync user):
#   bash scripts/validate.sh
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV=/opt/huesync/.venv
FIFO=/run/huesync/airplay.pcm
RUNDIR=/run/huesync
SPS_CONF=/usr/local/etc/shairport-sync.conf

PASS=0
FAIL=0
WARN=0

_pass() { echo "  [PASS] $*"; (( PASS++ )) || true; }
_fail() { echo "  [FAIL] $*"; (( FAIL++ )) || true; }
_warn() { echo "  [WARN] $*"; (( WARN++ )) || true; }
_info() { echo "  [INFO] $*"; }

echo ""
echo "==> HueSync deployment validation"
echo "    repo: $REPO_DIR"
echo "    HEAD: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo ""

# ---------------------------------------------------------------------------
# [1] Python environment
# ---------------------------------------------------------------------------
echo "[1] Python environment"

if [[ ! -x "$VENV/bin/python" ]]; then
    _fail "virtualenv not found at $VENV"
else
    _pass "virtualenv present: $VENV"

    if "$VENV/bin/python" -I -B -c "import soxr" 2>/dev/null; then
        SOXR_VER=$("$VENV/bin/python" -I -B -c "import soxr; print(soxr.__version__)" 2>/dev/null || echo "?")
        _pass "soxr importable (version: $SOXR_VER)"
    else
        _fail "soxr not importable — run: sudo ./scripts/install-huesync.sh"
    fi

    # Exercise the public synchronous canonical API with generated PCM only.
    # No worker is started and no live source or FIFO is opened.
    if "$VENV/bin/python" -I -B - <<'CANONICALCHECK'
import numpy as np
from huesync.canonicalizer import AnalysisPcmFrame
from huesync.models import Profile
from huesync.spectrum_engine import make_spectrum_engine
from huesync.sync_engine import CanonicalAnalysisPipeline

class NoLiveSource:
    def read(self):
        raise AssertionError("Validation must not read live PCM")

profile = Profile(bars_source="pcm_pipeline", spectrum_backend="v2")
engine = make_spectrum_engine(
    profile.spectrum_backend, n_bars=profile.bars,
    lower_hz=profile.lower_cutoff_freq, upper_hz=profile.higher_cutoff_freq,
)
pipeline = CanonicalAnalysisPipeline(
    source=NoLiveSource(), engine=engine,
    onset_method=profile.onset_method, onset_delta=profile.onset_delta,
    onset_alpha=profile.onset_alpha, superflux_mu=profile.superflux_mu,
    superflux_lag=profile.superflux_lag, bass_hz=profile.bass_hz, mid_hz=profile.mid_hz,
)
try:
    tone = (.25 * np.sin(2 * np.pi * 440 * np.arange(4800) / 48000)).astype(np.float32)
    samples = np.column_stack((tone, tone))
    records = pipeline.feed(AnalysisPcmFrame(
        samples=samples, sample_pos=0, epoch_id="validation", source_id="synthetic",
        over_range=False,
    ))
    records.extend(pipeline.end_of_stream())
    assert records, "Canonical pipeline produced no publications"
    assert all(r.epoch == "validation" and r.effective_spectrum_backend == "v2"
               for r in records)
    assert any(np.any(np.asarray(r.features.bars) > 0) for r in records), "No Spectrum signal"
    assert all(np.isfinite(r.features.bars).all() for r in records)
    queued = pipeline.drain_publications()
    assert len(queued) == len(records) and all(a is b for a, b in zip(queued, records))
    assert pipeline.latest() is not None, "Terminal publication disappeared at EOS"
    print(f"Canonical PCM → V2 + Beat → PublicationRecord: {len(records)} publications")
finally:
    assert pipeline.stop(), "Synchronous validation did not close cleanly"
CANONICALCHECK
    then
        _pass "Canonical analysis feed/EOS/publication smoke test passed (synthetic PCM)"
    else
        _fail "Canonical analysis smoke test failed — run: sudo ./scripts/install-huesync.sh"
    fi

    # cavacore native library
    _SO=$("$VENV/bin/python" -I -B -c \
        'from pathlib import Path; import huesync.cavacore; print(Path(huesync.cavacore.__file__).parent / "_libcavacore.so")')
    if [[ -f "$_SO" ]]; then
        _pass "cavacore native library present: $_SO"

        # ldd: verify FFTW resolves at load time
        if command -v ldd &>/dev/null; then
            _LDD=$(ldd "$_SO" 2>/dev/null || true)
            _FFTW_LINE=$(echo "$_LDD" | grep -i "fftw" || true)
            if [[ -z "$_FFTW_LINE" ]]; then
                _warn "ldd output contains no fftw entry — unexpected (library may be statically linked)"
            elif echo "$_FFTW_LINE" | grep -q "not found"; then
                _fail "libfftw3.so not resolved: $_FFTW_LINE"
                _info "  Fix: sudo ./scripts/install-huesync.sh"
            else
                _pass "FFTW resolves: $(echo "$_FFTW_LINE" | sed 's/^[[:space:]]*//')"
            fi
        else
            _warn "ldd not found — cannot verify FFTW runtime linkage"
        fi

        # Python import check + native smoke test (init + idempotent close)
        if "$VENV/bin/python" -I -B - 2>/dev/null <<'PYCHECK'
from huesync.cavacore import CavaCoreBackend, is_cavacore_available
assert is_cavacore_available(), "is_cavacore_available() returned False"
b = CavaCoreBackend(n_bars=30, rate=48000, channels=2)
b.close()
b.close()  # idempotent
print("OK")
PYCHECK
        then
            _pass "CavaCoreBackend native smoke test passed"
        else
            _fail "CavaCoreBackend native smoke test failed — run: sudo ./scripts/install-huesync.sh"
        fi
    else
        _fail "cavacore native library not found: $_SO"
        _info "  Fix: sudo ./scripts/install-huesync.sh  (rebuilds the installed native wheel)"
    fi
fi

# ---------------------------------------------------------------------------
# [2] Services
# ---------------------------------------------------------------------------
echo ""
echo "[2] Services"

for svc in huesync shairport-sync nqptp; do
    STATUS=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
    case "$STATUS" in
        active)   _pass "$svc: active" ;;
        inactive) _warn "$svc: inactive (not running)" ;;
        not-found|failed)
                  if [[ "$svc" == "huesync" ]]; then
                      _fail "$svc: $STATUS"
                  else
                      _info "$svc: not installed (AirPlay not configured)"
                  fi
                  ;;
        *)        _warn "$svc: $STATUS" ;;
    esac
done

# ---------------------------------------------------------------------------
# [3] Shairport Sync PCM source contract
# ---------------------------------------------------------------------------
echo ""
echo "[3] Shairport Sync PCM source contract"

if [[ -f "$SPS_CONF" ]]; then
    _pass "config exists: $SPS_CONF"
    for check in \
        'output_rate = 44100' \
        'output_format = "S16_LE"' \
        'output_channels = 2' \
        '/run/huesync/airplay.pcm' \
        'output_backend = "pipe"'; do
        if grep -qF "$check" "$SPS_CONF"; then
            _pass "  config contains: $check"
        else
            _fail "  config MISSING: $check — re-run: sudo ./scripts/install-huesync.sh"
        fi
    done
else
    _info "shairport-sync config not found at $SPS_CONF (AirPlay not configured)"
fi

# ---------------------------------------------------------------------------
# [4] Runtime directory
# ---------------------------------------------------------------------------
echo ""
echo "[4] Runtime directory /run/huesync"

if [[ -d "$RUNDIR" ]]; then
    RUNDIR_OWNER=$(stat -c '%U' "$RUNDIR" 2>/dev/null || echo "?")
    RUNDIR_PERMS=$(stat -c '%a' "$RUNDIR" 2>/dev/null || echo "?")
    _pass "$RUNDIR exists (owner: $RUNDIR_OWNER  mode: $RUNDIR_PERMS)"
    if [[ "$RUNDIR_OWNER" == "huesync" ]]; then
        _pass "  owner is huesync"
    else
        _fail "  owner is '$RUNDIR_OWNER', expected 'huesync'"
    fi
else
    _fail "$RUNDIR does not exist"
    _info "  Fix: run the repository installer or"
    _info "       run: systemd-tmpfiles --create /etc/tmpfiles.d/huesync-run.conf"
fi

# ---------------------------------------------------------------------------
# [5] AirPlay FIFO
# ---------------------------------------------------------------------------
echo ""
echo "[5] AirPlay FIFO $FIFO"

if [[ -e "$FIFO" ]]; then
    if [[ -p "$FIFO" ]]; then
        FIFO_OWNER=$(stat -c '%U' "$FIFO" 2>/dev/null || echo "?")
        FIFO_PERMS=$(stat -c '%a' "$FIFO" 2>/dev/null || echo "?")
        _pass "FIFO is a named pipe (owner: $FIFO_OWNER  mode: $FIFO_PERMS)"
        if [[ "$FIFO_OWNER" == "huesync" ]]; then
            _pass "  owner is huesync"
        else
            _warn "  owner is '$FIFO_OWNER' (expected 'huesync' — shairport-sync must run as huesync)"
        fi
    else
        _fail "$FIFO exists but is NOT a named pipe (type: $(stat -c '%F' "$FIFO" 2>/dev/null || echo '?'))"
    fi
elif [[ -d "$RUNDIR" ]]; then
    # /run/huesync directory exists but FIFO is absent.  setup-airplay.sh
    # provisions the FIFO via tmpfiles.d ('p' entry); if it is missing, the
    # old tmpfiles.d (directory only) is in place — re-run setup-airplay.sh.
    _warn "$FIFO not present — re-run: sudo ./scripts/install-huesync.sh"
    _info "  (The 'p' tmpfiles.d entry pre-creates the FIFO at boot so HueSync"
    _info "   can open it before iOS connects.)"
else
    _info "$FIFO not checked — AirPlay not configured (/run/huesync absent)"
fi

# ---------------------------------------------------------------------------
# [6] FIFO consumer audit — FD inspection, no PCM consumed
# ---------------------------------------------------------------------------
echo ""
echo "[6] FIFO consumer audit (FD inspection only)"

if [[ -p "$FIFO" ]]; then
    if command -v lsof &>/dev/null; then
        # lsof reads /proc kernel FD tables; does not read FIFO content.
        CONSUMERS=$(lsof "$FIFO" 2>/dev/null | tail -n +2 || true)
        N=$(echo "$CONSUMERS" | grep -c . 2>/dev/null || true; echo 0)
        # Normalize: grep -c returns 1 on empty, so clamp to 0.
        [[ -z "$CONSUMERS" ]] && N=0
        if [[ "$N" -eq 0 ]]; then
            _info "FIFO has 0 open FDs — no active AirPlay session (expected when idle)"
        elif [[ "$N" -le 2 ]]; then
            _pass "FIFO has $N open FD(s) — AirPlay path active"
        else
            _fail "FIFO has $N open FDs — possible duplicate reader"
            echo "$CONSUMERS" | head -5
        fi
    else
        # Fallback: count via /proc/*/fd without opening the FIFO.
        FIFO_REAL=$(realpath "$FIFO" 2>/dev/null || echo "$FIFO")
        N=$(find /proc/[0-9]*/fd -maxdepth 0 -type d 2>/dev/null | while read -r fddir; do
            find "$fddir" -maxdepth 1 -type l 2>/dev/null | while read -r fd; do
                target=$(readlink "$fd" 2>/dev/null || true)
                [[ "$target" == "$FIFO_REAL" ]] && echo 1
            done
        done | wc -l)
        if [[ "$N" -eq 0 ]]; then
            _info "FIFO has 0 open FDs — no active AirPlay session (/proc inspection)"
        elif [[ "$N" -le 2 ]]; then
            _pass "FIFO has $N open FD(s) — AirPlay path active (/proc inspection)"
        else
            _fail "FIFO has $N open FDs — possible duplicate reader (/proc inspection)"
        fi
    fi
else
    _info "FIFO not present — consumer audit skipped"
fi

# ---------------------------------------------------------------------------
# [7] tmpfiles.d entry (survives reboot)
# ---------------------------------------------------------------------------
echo ""
echo "[7] Boot-time runtime directory provisioning"

TMPFILES=/etc/tmpfiles.d/huesync-run.conf
if [[ -f "$TMPFILES" ]]; then
    _pass "tmpfiles.d entry exists: $TMPFILES"
    if grep -qF "d /run/huesync" "$TMPFILES"; then
        _pass "  'd' entry: creates /run/huesync directory at boot"
    else
        _warn "  directory entry missing — re-run: sudo ./scripts/install-huesync.sh"
    fi
    if grep -qF "p /run/huesync/airplay.pcm" "$TMPFILES"; then
        _pass "  'p' entry: pre-creates AirPlay FIFO at boot"
    else
        _warn "  FIFO 'p' entry missing — re-run: sudo ./scripts/install-huesync.sh"
        _info "  (Without it, the FIFO only exists after an iOS client connects)"
    fi
else
    _warn "No tmpfiles.d entry at $TMPFILES"
    _info "  /run/huesync and the AirPlay FIFO will not exist after reboot"
    _info "  Run: sudo ./scripts/install-huesync.sh"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Summary"
echo "    PASS: $PASS   FAIL: $FAIL   WARN: $WARN"
echo ""

if (( FAIL > 0 )); then
    echo "Some checks FAILED. Resolve [FAIL] items above."
    exit 1
else
    echo "All checks PASSED."
    exit 0
fi
