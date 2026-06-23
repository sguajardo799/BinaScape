# Output Analyzer

`output-analyzer` builds metadata-only analysis artifacts for an orchestrator run directory. It joins render sidecars with scene manifests, summarizes degraded-scene metadata, writes normalized data files, generates SVG chart images, and produces a self-contained HTML report.

## Usage

```powershell
uv run --project apps/output-analyzer output-analyzer analyze outputs/sim_001
```

By default the app writes to `<run-dir>/analysis`. Use `--output-dir` to choose another location.

```powershell
uv run --project apps/output-analyzer output-analyzer analyze outputs/sim_001 --output-dir outputs/sim_001/analysis
```

## Inputs

The analyzer reads these files when present:

- `outputs/render/**/__render.json`
- `manifests/scene/*.json`
- `outputs/degraded/**/*.json`
- `indexes/render_index.jsonl`
- `indexes/clarity_index.jsonl`

It does not inspect WAV audio. Degraded outputs are summarized from metadata only.

## Outputs

The analysis directory contains:

- `summary.json`
- `render_scenes.json`
- `joined_render_sources.jsonl`
- `joined_render_sources.csv`
- `degraded_metadata.jsonl`
- `degraded_metadata.csv`
- `images/heatmap_locations_*.svg` with numbered room-position axes
- `images/heatmap_receiver_locations.svg`
- `images/hist_room_dimensions.svg`
- `images/hist_room_area.svg`
- other `images/*.svg` charts
- `report.html`

The report includes source-location heatmaps by event type, a receiver-location heatmap, histograms for DoA, receiver-relative DoA, distance, elevation, reverberation time, explicit room width/length/area distributions, a combined room-dimensions distribution chart, employed HRTF counts, and degraded metadata summaries.
