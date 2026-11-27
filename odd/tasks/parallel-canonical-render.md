# Parallel canonical render

Objective: render canonical variants of different scenes concurrently, bounded by `execution.num_workers`.
Branch: `feat/workers`. Delivery: ask-on-risk. Tests: `apps/orchestrator/.venv/Scripts/python.exe -m pytest` (from `apps/orchestrator`).

## Specs

- S1: With `num_workers > 1`, up to `num_workers` scenes have their canonical MATLAB attempt running at the same time. User: "corrí @configs/experiments/static_example.yml para 2 3 y 4 workers" showed peak MATLAB concurrency 1 for every worker count.
- S2: Workers only run `_run_single_render_task`; the main thread remains the single owner of the variant index, retry chains, batch circuit, archives, scene manifests and static plan revisions.
- S3: Per-scene behavior is unchanged: canonical reuse on resume, MATLAB process retries, RAVEN T30 rejection → archive + deterministic resample + manifest/plan revision, sampling failure recording.
- S4: Results do not depend on worker count: accepted manifest order, other-variant task order and RAVEN sampling failures are ordered by scene index (`failed_scene_indices` is already sorted). Exceptions (accepted, audit logs written durably as they happen): `scene_revision_history` and the `scene_sampling_failures.jsonl` journal keep completion order.
- S5: On the first error or an open circuit, no new scene, retry or resample is dispatched; in-flight attempts finish and are resolved; then `RenderStaticRunError` is raised as today.
- S6: `num_workers: 1` keeps the existing sequential path.
- S7: Other-variants phase (`_run_render_tasks`) is unchanged.
- S8: Real run of `static_example.yml` with 1 vs 4 workers: scene manifests identical and static plan identical except run-path-derived hashes; rendered audio within an aggregate T30 tolerance (user choice: "Agregada ≤5%": run-level mean T30 and its standard deviation within 5% between both runs), not byte-identical, because RAVEN ray tracing is not reproducible across runs; peak MATLAB concurrency 4; render wall time reported. User: "mientras cambia la spev a dentro de tolerancia en un futuro resolvere si asegurar replicabilidad".
## Tasks

- [x] T1 (S1-S7) inline — extract per-scene round/after-canonical steps, add bounded canonical dispatcher, tests RED→GREEN, docs. Commit: 1c826d2.
- [x] T2 (S1-S7) delegated verifier — Risk item 4 (concurrency). S1-S4, S6, S7 PASS; S5 blocker fixed. Commit: see L5.
- [x] T3 (S8) user-run — PASS on 91 common scenes (see L6-L9): peak concurrency 4, render wall time reported, manifests identical, aggregate T30 within 5% (mean 0.3%, SD 1.0%).

## Log

- L1 (2026-11-01, user): "Se necesita mejorar o refactorizasr la implementacion de workers en la planificacion y renderizado de las escenas acústicas" (planning part delivered in commit da44858).
- L2 (2026-11-01, user): "si prepara el plan para la paralelización del rendeer" → plan presented in chat; user: "si, implementalo".
- L3: Route: T1 inline (one behavior, no Writer trigger reason). Engram mirror pending: Engram tools unavailable in this session.
- L4: T1 evidence — RED on base: `test_canonical_renders_of_different_scenes_overlap` (BrokenBarrierError) and `test_canonical_failure_drains_inflight_and_dispatches_nothing_new` failed; GREEN after change: 4/4; full suite 982 passed, 1 env failure (matplotlib). T2 verifier launched on 1c826d2.
- L5: T2 verifier on 1c826d2 — blocker B1 (S5): an exception in main-thread bookkeeping (`advance`/`complete`, e.g. Windows PermissionError while archiving) left in-flight canonical chains `running`, blocking the next run. Fix: record it as the first error and keep draining. Regression `test_bookkeeping_error_still_resolves_inflight_canonicals` RED→GREEN; full suite 983 passed, 1 env failure. Advisories kept: a scene halted during shutdown keeps its rejected canonical (no archive/resample, as S5 requires); progress may count a non-dispatched retry as "fallidas".
- L6 (2026-11-01): T3 evidence, `outputs/e2e_1_worker` (interrupted by shutdown: 91/100 rendered, 1 chain left `running`, no degradation stage) vs `outputs/e2e_4_workers` (100/100). Peak MATLAB concurrency 1 vs 4; render 1.98 vs 4.04 scenes/min (about 2.04x, -51% render time); mean attempt 17 s vs 37 s (CPU contention). Scene manifests 100/100 identical after run-name normalization; static plan identical except `contract_hash` and per-scene `functional_hash`, which hash run-specific paths. WAVs: 0/91 byte-identical; 76/91 differ by at most 1 LSB, 15/91 differ substantially; T30 differs in every compared scene.
- L7 (2026-11-01): RAVEN exposes no seed (toolbox, `.rpf`, binaries); `fixReflectionPattern = 1` is already set (`render_static_scene.m:103`) and runs still differ. Hypothesis, untested: OpenMP thread scheduling in RavenConsole (suggested probe for the user: `OMP_NUM_THREADS=1`). User (2026-11-01): "mientras cambia la spev a dentro de tolerancia en un futuro resolvere si asegurar replicabilidad" → S8 rewritten.
- L8 (2026-11-01): User chose T30 tolerance "Agregada ≤5%" (on 91 common scenes: run mean T30 0.524 s vs 0.526 s, 0.3%; SD 0.191 vs 0.189) → S8 updated. Completing the 1-worker run: `reconcile-static` applied to chain 7304c8ce (scene 92), then `render-static` with a temporary copy of the config (`resume_if_possible: true`, excluded from the plan contract). Scene 92 failed twice in MATLAB: `MATLAB:load:unableToReadMatFile` on `%APPDATA%\MathWorks\MATLAB\R2025b\matlabprefs.mat` (313 bytes, last written 18:11:15, the moment of the shutdown), raised from `ita_preferences` via `itaAudio`. Environment blocker, not a code defect. Advisory: the ITA toolbox calls `setpref` during renders, so concurrent MATLAB workers share one prefs file.
- L9 (2026-11-01): Prefs repaired: corrupt `matlabprefs.mat` renamed to `matlabprefs.mat.bak`, ITA `lastToolboxSetupVerNum` recorded in batch (non-interactive equivalent of `ita_toolbox_setup` line 90); `itaAudio` loads without warnings. A second resume stopped with `RetryBlockedError: same-input retry chain is terminal (exhausted)` for scene 92, as documented (README: only a new run renews an exhausted chain). User chose "Cerrar con 91 escenas": S8 verdict on the 91 common scenes, mean T30 0.524 s vs 0.526 s (0.3%), SD 0.191 vs 0.189 (1.0%), within the 5% aggregate tolerance. The 1-worker run stays at 91/100 without the degradation stage. Feature closed.
