# Circular-array scene examples

Experiment examples use the current room geometry schema:

- `configs/experiments/circular_array_only.yml`: array only, no receiver IR catalog,
  no Clarity processing.
- `configs/experiments/circular_array_mixed.yml`: binaural HRTF plus array;
  Clarity selects only `binaural_hrtf`. Add HARTF outputs only with real DAFF
  assets; never add HARTF to degradation targets.

The following are receiver-section examples, not complete scene
files. Generate full scenes from the experiment configs rather than combining
these excerpts with obsolete room-dimensions manifests.

## Array-only scene receiver (four-channel illustration)

```json
{
  "receiver_id": "listener_001",
  "position_m": [2.1, 1.5, 1.8],
  "orientation_deg": {"yaw": 0, "pitch": 0, "roll": 0},
  "hrtfs": [],
  "circular_array": {
    "output_type": "circular_array",
    "num_microphones": 4,
    "radius_m": 0.05,
    "center_m": [2.1, 1.5, 1.8],
    "yaw_deg": 0,
    "microphones": [
      {"microphone_index": 0, "channel_index": 1, "position_m": [2.15, 1.5, 1.8]},
      {"microphone_index": 1, "channel_index": 2, "position_m": [2.1, 1.5, 1.85]},
      {"microphone_index": 2, "channel_index": 3, "position_m": [2.05, 1.5, 1.8]},
      {"microphone_index": 3, "channel_index": 4, "position_m": [2.1, 1.5, 1.75]}
    ]
  }
}
```

Microphone indices start at zero; WAV channels start at one. Positions are
public coordinates (Y vertical), with yaw zero beginning at +X and channel
ordering toward +Z. Pitch and roll do not tilt the array. MATLAB reflects Z
exactly once into RAVEN coordinates.

## Mixed scene receiver

Keep the same array descriptor, but populate `hrtfs` with actual catalog entries:

```json
[
  {"hrtf_id": "binaural_hrtf", "hrtf_path": "C:/catalog/hrtf.daff"},
  {"hrtf_id": "bte_rear_hartf", "hrtf_path": "C:/catalog/rear.daff"}
]
```

Scene generation retains all enabled receiver descriptors. A runtime manifest
selects one output; it must not use an array descriptor as a fake HRTF file.
For reruns, enable both `resume_if_possible` and `overwrite_existing` to permit
scene-manifest regeneration while validating existing audio before reuse.
Array sources are downmixed to mono by channel mean before normalization/gain;
catalog-backed source handling remains unchanged.
