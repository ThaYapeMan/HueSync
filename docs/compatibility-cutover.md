# Current-schema cutover inventory

This document records the explicit migration boundary, not runtime alias support.
Classification: A current required feature; B historical documentation; C one-time
migration; D removed active compatibility debt; E intentional legacy feature.

| Location / concept | Class | Action / current contract |
|---|---|---|
| Models: historical Coupling reference names | C/D | Removed from from_dict; migration renames with conflict checks |
| Storage: historical EnergyProfile collection | C/D | Current `energy_profiles`; migration converts once |
| Storage: historical Effect collection | C/D | Current `effects`; migration converts once |
| Storage: old player/zone/analyser collection names | C/D | Only migration recognizes them |
| Models: historical Effect/color-mode names | C/D | No silent fallback; migration renames or fails |
| Models: old EnergyProfile blend/effect reference fields | C/D | Migration only |
| Models: old VirtualPlayer name | C/D | Migration retains it as display_name; conflicts fail |
| Models: Profile old names and unknown-key filtering | D | Removed; Profile is a current internal runtime copy |
| API: retired entity-name routes | D | Removed; current request schemas forbid unknown fields |
| WebSocket status / manager active_color_mode | D | Current field/property is effect_type / active_effect; frontend rebuilt |
| ColorMode / COLOUR_MODES two-effect selector | D | Unused enum/selector removed; current Effects registry remains |
| PublicationRecord.effective_engine_id | D/A | Name removed; required identity retained as effective_spectrum_backend |
| CAP._engine / CAP._onset_pipeline forwarding | D | Removed; owners are SpectrumProcessor and BeatDetector state |
| BeatDetector private forwarding properties | D | Removed; tests inspect coherent state directly |
| CAP unused pending-onset compatibility list | D | Removed; live interval/history publication behavior unchanged |
| V2/CAVA whole-pipeline compatibility wrappers | D | Removed; tests/production use CanonicalAnalysisPipeline |
| Unused old mono PcmAudioPipeline | D | Removed with tests solely for that superseded implementation |
| Unused mono AirPlayPipeSource | D | Removed; stereo production adapter and regressions retained |
| PcmSource / SqueezeliteShmSource / PcmStft / HPSS tap | E | Required by supported external CAVA/FIFO feature |
| SpectrumProcessor._engine | A | Real algorithm ownership, not an alias |
| SpectrumEngine.feed/flush and SharedAnalysis projection | A | Current common engine contract, including synchronous CAVA use |
| Profile / BridgeConfig / HueOutputConfig | A | Current runtime/output adapters with production callers |
| ColourModeEffect / AudioFeatures onset snapshots | A | Actual Effects/lighting path, not historical deserialization |
| Acceptance CSV effective_backend/effective_engine columns | A | Existing exported diagnostic format; values read current record identity |
| Historical offline comparison scripts and archived prompts | B | Evidence/tools for stated historical algorithms, not runtime architecture |

The 18 removed debt groups above do not remove the intentional external-FIFO mode.
Its separate producer keeps the upstream SHM layout expected by external CAVA;
canonical LMS requires the v1 producer. The installer builds both from one upstream
pin. No DSP, SHM v1, processor lifecycle or publication policy was redesigned.

## Migration safety

Schema version 1 is required by current runtime. Migration validates all known fields,
IDs and nonempty references before rewrite, backs up exact original bytes, and uses
atomic replacement. Equal aliases can be normalized; unequal aliases fail. Unknown
collections/fields and unsupported pre-entity data are not silently dropped. Runtime
must be stopped; the installer holds an exclusive installation lock. Repeated current
migration is a no-op. See [installation](installation.md#migration) for backup paths.

## Verification boundary

Local checks include migration failure/idempotency fixtures, current-schema rejection,
installer command/metadata tests, frontend build/tests, pinned producer patch/objects/
full link with extracted Debian libraries, and wheel/native smoke execution. These do
not prove a fresh booted Debian LXC install, service permissions, host ALSA passthrough,
AirPlay timing, or realtime behavior. Those remain target acceptance tasks.
