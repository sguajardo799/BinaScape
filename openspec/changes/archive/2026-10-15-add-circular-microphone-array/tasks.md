# Tasks

## 1. MATLAB/RAVEN response contract verification

- [x] 1.1 Characterize the installed RAVEN omnidirectional RIR API with at least two sources and four receiver positions; verify source/microphone ordering, absolute arrival timing, 44100 Hz responses, and T30 retrieval, and record the API and dimension mapping in a focused MATLAB fixture/test.
- [x] 1.2 Select the verified batched-receiver adaptation or deterministic per-microphone RIR loop; document its limits in the MATLAB README and verify that it preserves relative delay/level with an asymmetric source fixture. Report a blocker rather than substitute BRIRs or duplicate center responses if neither adaptation meets the contract.

## 2. Orchestrator configuration and eligibility validation

- [x] 2.1 Add optional array configuration and make binaural configuration optional in `config/models.py`; adapt `loader.py` to avoid array catalog resolution, with model/loader tests for omitted/disabled arrays, valid array-only configs, strict N >= 2, finite positive radius, and derived channel count.
- [x] 2.2 Extend receiver enumeration and semantic validation in `validator.py`, including safe subdirectories, required/enabled consistency, and at least one enabled output; verify array-only, mixed, no-output, and existing HRTF/HARTF rendering configurations in validator tests.
- [x] 2.3 Restrict degradation targets to `binaural_hrtf`, reject HARTF/arrays even when degradation is disabled, and require enabled HRTF targets when degradation is enabled; add tests for both HARTF types, N=2 arrays, unknown targets, empty targets, and absent/disabled HRTF.
- [x] 2.4 Document YAML fields, defaults, array-only degradation settings, and the HARTF Clarity migration in the orchestrator README; verify examples against the config model and validator with fixture-backed paths.

## 3. Orchestrator geometry and scene manifests

- [x] 3.1 Add a small public-coordinate circular geometry helper and integrate it into receiver sampling; verify four/eight microphone counts, radius, yaw 0/90, stable ordering, and pitch/roll invariance with geometry tests.
- [x] 3.2 Validate every microphone's containment and source distance through existing bounded sampling retries; verify shoebox, trapezoid, and L-shaped edge cases plus exhausted-attempt diagnostics in sampling tests.
- [x] 3.3 Extend `scene_builder.py` with ordered `receiver.circular_array` descriptors, empty HRTF lists for array-only runs, and deterministic primary output selection; verify array-only and combined manifests without fake catalog paths and retain multi-HRTF regression tests.
- [x] 3.4 Add current-schema array-only and mixed experiment/scene examples, reconciling affected local examples' obsolete room dimensions with supported room geometry; verify their documented geometry/channel mapping and validate runnable config fixtures.

## 4. MATLAB manifest validation and array rendering

- [x] 4.1 Extend `validate_static_scene_config.m` and output normalization/naming for array descriptors, explicit active output selection, and legacy HRTF-only manifests; verify invalid geometry/order, array-only, mixed direct rendering, and legacy schema fixtures in MATLAB tests.
- [x] 4.2 Add array receiver construction using ordered public positions and one Z reflection, without active HRTF/directivity dependencies; extend the RAVEN mock and verify positions, yaw 0/90, horizontal layout, and RIR/BRIR selection in receiver tests.
- [x] 4.3 Implement the verified RIR retrieval adaptation and normalize source/microphone responses into ordered multichannel convolution inputs; verify it against mocked distinguishable responses and the response-contract fixture from group 1.
- [x] 4.4 Generalize static convolution/mixing to expected receiver channel count, downmixing array sources by arithmetic mean before normalization/gain and recording the source channel policy, while preserving source gain/offsets, duration/tail handling, and global clipping protection; verify mono/stereo inputs, N=2/4/8, relative delay/level preservation, and existing binaural mixing regressions.
- [x] 4.5 Retain mono/N-channel noise fitting and clear mismatched-channel errors; verify replication, exact channel mapping, SNR handling, and stereo-to-eight-channel rejection in MATLAB noise tests.
- [x] 4.6 Update the static entrypoint, run/batch summaries, and metadata exporter to use output identity without fake array HRTF fields; verify N-channel WAV export, ordered microphone provenance, coordinate labels, and preserved room T30/source summaries in MATLAB export tests.
- [x] 4.7 Document the array render path, channel convention, backend requirements, noise limitations, and unchanged unsupported dynamic status in Spanish in the MATLAB README; verify documented examples load through the static entrypoint contract.

## 5. Orchestrator runtime planning, indexing, and resume

- [x] 5.1 Generalize variant enumeration and path resolution in `output_paths.py`, `matlab_runner.py`, and `render_pipeline.py` without a plugin framework; verify distinct array/HRTF/HARTF identities, numbered HRTFs, and absence of array IR file requirements in path/planning tests.
- [x] 5.2 Write single-output runtime manifests with explicit active selection and correct array/HRTF isolation while preserving prepared source rewrites; verify array-only and mixed runtime manifests and existing runtime-audio tests.
- [x] 5.3 Extend `output_index.py` with array identity, nullable array `receiver_ir_path`, actual WAV channel/sample-rate checks, and planned geometry provenance matching; verify valid outputs, wrong shape, stale radius/yaw/order, metadata-disabled runtime provenance, and old catalog index compatibility.
- [x] 5.4 Integrate arrays into resumed planning, worker dispatch, and canonical RAVEN T30 acceptance/retry handling; verify array-only completed/partial/failed/resumed states and T30 metadata rejection with focused pipeline tests.
- [x] 5.5 Inspect output-analyzer readers for HRTF-only metadata assumptions and add compatibility fixtures for array sidecars/indexes; make only necessary compatibility adjustments and verify existing reports still work without introducing new analyzer features.
- [x] 5.6 Document array artifact layout, identity, and resume provenance in the orchestrator README; verify described output paths match array path/index tests.

## 6. Orchestrator Clarity handoff safeguards

- [x] 6.1 Enforce HRTF-only eligibility at Clarity job creation and submission/issue validation, preserving numeric HRTF suffix handling; verify stale HARTF/array jobs are blocked independently of config validation and that valid numbered HRTFs remain eligible.
- [x] 6.2 Update mixed-run handoff tests to prove all enabled receiver outputs render but only completed valid selected HRTFs generate jobs; verify array-only runs generate none and existing degraded filename/profile-directory contracts remain intact.
- [x] 6.3 Update Clarity-facing examples/docs that currently target HARTF and retain the standalone stereo MSBG contract; verify HRTF-only examples validate and run existing clarity-backend regression tests without changing its algorithm.

## 7. Cross-component acceptance

- [x] 7.1 Run the orchestrator Python suite and MATLAB unit suite; verify all new spec scenarios and catalog-render regressions pass, recording any environment-dependent checks separately from mocked results.
- [x] 7.2 Run installed-RAVEN array-only and mixed HRTF/HARTF/array smoke scenes with asymmetric source geometry; verify channel count/order, relative timing, public/backend coordinate provenance, output indexing, T30 acceptance, and successful resume.
- [x] 7.3 Exercise mixed rendering with Clarity enabled for HRTF and explicitly invalid HARTF/array target configurations; verify only HRTFs produce degraded artifacts and invalid targets fail before rendering/submission.
- [x] 7.4 Review implementation against both delta specs and the design, including the intentional HARTF compatibility break; verify documented examples and all tracked tasks are complete before requesting archive.
