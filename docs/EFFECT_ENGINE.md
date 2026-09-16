# Effects and output boundaries

Current reference. Historical effect-design prompts do not override this document.

Canonical analysis publishes AudioFeatures through PublicationRecord. External FIFO
analysis also produces AudioFeatures. Effects consume those generic features; they
do not select Spectrum engines or branch on Beat algorithm identity.

Effects produce Scenes. Output drivers sample Scenes at their configured light
positions and convert colours into hardware-specific commands. Hue Entertainment
protocol objects belong to the Hue driver. Keep those concerns out of analysis and
Effects.

EnergyProfile selects high/low-energy Effects and blend settings. LayerMixer/render
state operates at the rendering cadence, independently of native Spectrum execution
cadence. Analysis parameter changes route to their owner; rendering changes do not
implicitly rebuild the analysis architecture.

`latest()` provides the freshest audio interval, ordered by `(sample_end, sample_start)`
within an epoch; exact ties prefer later delivery. Delayed historical contributions
remain in the delivery queue but do not rewind the live Effects snapshot. Polling
is not guaranteed delivery of every event, including historical onsets; record-aware
consumers use sequence and the bounded queue. Carried Spectrum bars in those records
retain explicit provenance. See
[audio-pipeline.md](audio-pipeline.md).

Loudness/Chroma extension protocols are not production algorithms. Legacy HPSS/tap
behavior must not be described as a shipped canonical processor. Use the actual API
schema for available Effect/settings fields and [configuration.md](configuration.md)
for entity ownership.

Visual quality, target render backlog and realtime behavior **REQUIRE LXC VALIDATION**.
