> **Historical document — not the current implementation contract.**
> This preserves an earlier proposal, review or measurement. Do not execute its
> migration/build instructions as current guidance. See [the frozen architecture](ANALYSIS_ARCHITECTURE.md),
> [configuration](configuration.md) and [testing](testing.md). Measurements apply
> only to their original commit/environment; current LXC validation is pending.

# HueSync — output latency: measuring and compensating

*Replaces v1. v1 proposed comparing LMS's reported position between the virtual
player and the listening player. That was tested and does not work — see below.
It also assumed a single strategy would fit all player types, which it does
not.*

---

## The problem

HueSync taps audio at the source. Whatever plays it back adds delay before the
listener hears anything, so the lights run ahead. How far ahead depends
entirely on the player.

## What was tested and rejected

**Comparing LMS positions between synced players does not work.** Measured with
a Sonos room and the HueSync virtual player synced together and playing:

```
SONOS::Study   time: 102.647383
HueSync        time: 102.648604
```

A difference of one millisecond — measurement noise, not buffering. LMS reports
the **group position**, which by definition is identical for every member of a
sync group; that is what synchronisation means in LMS's bookkeeping. The delay
introduced by sonos-squeezebox and Sonos sits entirely *beyond* the point LMS
knows about.

One useful finding did come out of it: LMS reports `sync_master` and
`sync_slaves` in its `status` response, so the sync group can be **discovered**
rather than configured.

---

## Player types differ fundamentally

This is the key realisation, and it means there is no single answer.

| Player | Latency | Stable? | Why |
|---|---|---|---|
| squeezelite + local DAC | tens of ms | yes | direct output, nothing in between |
| **AirPlay 1** | ~2.0–2.25 s | **yes** | latency is *negotiated* at connection and held by clock sync |
| **AirPlay 2** | ~0.5 s or less | **yes** | same mechanism, buffered-audio stream type |
| **Sonos via sonos-squeezebox** | ~2 s+ | **no** | opaque buffer, no timing negotiation, varies with WiFi conditions |

### Why AirPlay is stable and Sonos is not

AirPlay's latency is **specified by the source when it negotiates** with the
receiver — most sources settle on exactly two seconds; AirPlay 2's buffered
stream type uses half a second or less. The receiver then *maintains* that
figure by synchronising its clock to the source's, typically to within a
fraction of a millisecond, using PTP (AirPlay 2) or an NTP variant (AirPlay 1).

Network variation is precisely what that machinery exists to absorb. The
latency is a protocol guarantee, not a buffer that drifts.

`sonos-squeezebox` has no equivalent. It presents a continuous stream that
Sonos treats as a radio station, and Sonos buffers it as much as it needs to —
which on WiFi depends on congestion, interference and bandwidth. The offset
genuinely moves.

**So a fixed per-player setting is correct for AirPlay and local players, and
wrong for Sonos.** Both strategies are needed.

---

## Proposed abstraction: LatencyProbe

Mirroring `AudioSource` and `Output`:

```python
class LatencyProbe(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def current_delay_ms(self) -> int:
        """Best current estimate. Never blocks."""
```

| Implementation | Strategy | For |
|---|---|---|
| `NoLatencyProbe` | returns 0 | local squeezelite; below the threshold of perception |
| `FixedLatencyProbe` | returns a configured constant | AirPlay, and any player with negotiated timing |
| `UpnpPositionProbe` | polls the player's real position, continuously | Sonos |

Selection follows from the player: HueSync reads `sync_master` from the LMS
`status` response, looks up how that player is configured, and instantiates the
matching probe. Unknown players default to `FixedLatencyProbe` with 0 and a
note in the GUI.

---

## UpnpPositionProbe — the Sonos case

### What it does

Poll the Sonos player's own reported position and compare it with LMS's group
position:

```
delay = lms_group_position − sonos_reported_position
```

Both are already available:

- **LMS**: CLI on port 9090, `<mac> status - 1 tags:`, read `time:`.
  Remember the raw-MAC rule — URL-encoding it makes LMS silently fail to match
  the player.
- **Sonos**: UPnP `AVTransport::GetPositionInfo`, a SOAP POST to port 1400 on
  the speaker. Read-only, no side effects.

### Doing UPnP directly, not via sonos-squeezebox

HueSync should speak UPnP itself rather than asking sonos-squeezebox for the
figure. Two reasons: it keeps HueSync independent of that project (the same
reasoning that led to tapping audio at the source rather than depending on a
capture device), and `GetPositionInfo` is small enough not to warrant a
dependency.

It is a plain HTTP POST with an XML body — no UPnP library required:

```
POST /MediaRenderer/AVTransport/Control HTTP/1.1
Host: <speaker-ip>:1400
Content-Type: text/xml; charset="utf-8"
SOAPACTION: "urn:schemas-upnp-org:service:AVTransport:1#GetPositionInfo"

<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetPositionInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
      <InstanceID>0</InstanceID>
    </u:GetPositionInfo>
  </s:Body>
</s:Envelope>
```

The response contains `<RelTime>` in `H:MM:SS` form. Note the resolution: whole
seconds. That is coarse, which shapes the smoothing below.

### Finding the speaker's IP

The LMS `status` response gives `player_ip` — but for a sonos-squeezebox player
that is the *bridge* host (`192.168.178.31`), not the speaker. The speaker's
address has to come from elsewhere: SSDP discovery, or configured per player.
Configuring it is acceptable for a first version; the mapping rarely changes.

### Smoothing

Two things force this:

- `RelTime` has one-second resolution, so a single reading can be off by up to
  a second.
- The offset genuinely moves with network conditions, but slowly — over tens of
  seconds, not between frames.

So: poll every ~10 seconds, keep a rolling median over the last several
readings, and rate-limit how fast the applied delay may change (e.g. no more
than 100 ms per adjustment). Lights that jump because one poll came back odd
are worse than lights that are 200 ms out.

---

## Where the setting lives

Not on the profile. The delay belongs to the **pairing** of a light target and
a listening player:

```python
@dataclass
class PlayerLatency:
    player_mac: str
    strategy: str          # "none" | "fixed" | "upnp"
    fixed_delay_ms: int    # used when strategy == "fixed"
    speaker_ip: str | None # used when strategy == "upnp"
```

Stored globally, keyed by player MAC. On activation HueSync reads
`sync_master`, finds the entry, and constructs the probe.

The output layer's ring buffer already exists and applies whatever
`current_delay_ms()` returns.

---

## Implementation order

1. **`lms_status.py`** — query the CLI for position and sync group. Standalone,
   testable, and needed by everything else.
2. **`LatencyProbe` and `FixedLatencyProbe`** — plus the `PlayerLatency`
   storage and GUI. This alone covers local players and AirPlay correctly, and
   gives Sonos a usable manual value.
3. **`UpnpPositionProbe`** — the continuous measurement for Sonos.

Steps 1-2 are modest and deliver most of the benefit. Step 3 is the part that
handles WiFi variability, and can follow once the structure is in place.

## Sources

- AirPlay latency negotiated by the source, ~2 s classic / ~0.5 s AirPlay 2,
  maintained by clock synchronisation:
  https://github.com/mikebrady/shairport-sync
- AirPlay 2 stream types and their latencies:
  https://github.com/mikebrady/shairport-sync/blob/master/AIRPLAY2.md
- LMS group position finding: measured directly, see "What was tested and
  rejected" above.
