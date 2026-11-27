# BinaScape — Binaural Hearing Loss Pipeline

Monorepo for static binaural acoustic dataset generation and metadata analysis.
The orchestrator samples scenes and invokes MATLAB/RAVEN; optional Clarity MSBG
processing produces hearing-loss variants. Output Analyzer inspects the metadata
of an existing run independently of generation.

This documentation audits `develop@05f9b27` and the current derived checkout
`update-fix-output-analyzer@1a3b1c1`. The analyzer has additional capabilities in
this checkout; see the [revision comparison](docs/maintenance/documentation-audit.md#diferencias-del-analizador-entre-revisiones).
Static rendering is supported. Dynamic MATLAB entrypoints remain placeholders.

## Applications

| Application | Responsibility | Guide |
|---|---|---|
| `apps/orchestrator` | Experiment YAML, scene sampling, manifests, render coordination, optional Clarity handoff | [Orchestrator](apps/orchestrator/README.md) |
| `apps/matlab` | Static RAVEN/ITA rendering, WAV and render sidecars | [MATLAB](apps/matlab/README.md) |
| `apps/clarity-backend` | Explicit JSONL jobs, hearing profiles and Clarity/MSBG processing | [Clarity](apps/clarity-backend/README.md) |
| `apps/output-analyzer` | Optional post-run metadata analysis, HTML/SVG reports and JSON/JSONL/CSV exports | [Output Analyzer](apps/output-analyzer/README.md) |

The root `pyproject.toml` and `uv.lock` declare **three Python workspace members**:
orchestrator, clarity-backend and output-analyzer. MATLAB is outside the uv
workspace. All Python projects require Python `>=3.12,<3.13`.

## Start from the repository root

Install the workspace and development tools:

```sh
uv sync --all-packages --all-extras
```

Inspect the Python CLIs:

```sh
uv run --project apps/orchestrator acoustic-orchestrator --help
uv run --project apps/clarity-backend clarity-backend --help
uv run --project apps/output-analyzer output-analyzer --help
```

Generate scenes without invoking MATLAB (this writes run artifacts):

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

Render when MATLAB, ITA Toolbox, RAVEN and all local assets are configured:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Prepare or submit optional hearing-loss jobs:

```sh
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml
uv run --project apps/orchestrator acoustic-orchestrator clarity-handoff configs/experiments/static_example.yml --submit
```

Analyze an existing run, substituting its actual directory:

```sh
uv run --project apps/output-analyzer output-analyzer analyze outputs/sim_001
```

The analyzer reads metadata only; it does not open WAV contents or invoke a
renderer. Its default output directory is `<run-dir>/analysis`.

## Configuration and artifacts

[Static example](configs/experiments/static_example.yml) and
[fixed-position example](configs/experiments/speech_fixed_position_e2e.yml)
illustrate root-first execution. [Hearing catalogs](configs/hearing/hearing_profiles.yaml)
and [example profile](configs/hearing/example_profile.yaml) supply hearing-loss
settings. [Orchestrator examples](apps/orchestrator/examples/) and
[MATLAB examples](apps/matlab/example/) support local development.

These are examples with environment-dependent asset paths. The implementation
and validators define accepted behavior. Experiment paths resolve relative to
the YAML location; inspect the selected example before running it.

The [artifact flow](docs/architecture/data-flow.md) describes scene manifests,
runtime variants, WAV/sidecars, jobs and optional analysis. The
[contracts reference](docs/architecture/contracts.md) distinguishes producer and
consumer versions, coordinates and constraints.

## Development and documentation

Use the [documentation index](docs/README.md),
[architecture overview](docs/architecture/overview.md),
[setup guide](docs/development/setup.md) and
[testing guide](docs/development/testing.md).
[Historical PRD](docs/PRD/README.md) are CLOSED / IMPLEMENTED by project-owner
decision; their original before/after narratives are historical context.
The [audit](docs/maintenance/documentation-audit.md) records evidence, decisions,
branch differences and verification limits.

Full rendering requires a local MATLAB/RAVEN/ITA installation and referenced
WAV, HRTF/DAFF, directivity, material and RPF assets. MATLAB currently has a local
Windows project-path assumption. Effective MATLAB seeds do not certify RAVEN
bitwise determinism. See the application guides for these runtime limits.
