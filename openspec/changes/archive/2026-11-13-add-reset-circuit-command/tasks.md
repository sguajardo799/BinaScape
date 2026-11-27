# Tasks

## 1. Orchestrator: eligibility and inspection (read-only)

- [x] 1.1 Add a fixture in `apps/orchestrator/tests/` that reproduces the observed batch: open circuit (limit 5, 5 trailing failures), 4 `circuit_open` chains whose last attempt is `accepted` with outputs present, 1 `circuit_open` chain with `unknown_process_failure` at ordinal 1, 1 `scheduled` chain; reuse existing helpers from `test_interruption_recovery.py` / `test_render_retry_dispatch.py`
- [x] 1.2 Write RED tests for `reset_batch_circuit(..., apply=False)` in a new `pipeline/circuit_reset.py`: reports limit, streak, and only the failed chain as eligible (`scheduled`, 1 consumed / 2 remaining); the accepted chains are listed as untouched; no file and no lock is created (assert directory snapshot unchanged)
- [x] 1.3 Implement inspection and eligibility per design decision 3 (active chain, last attempt resolved, `unknown_process_failure`, `retry_decision=circuit_open`, observed integer exit code; `exhausted` when ordinal == `max_attempts`); observe GREEN
- [x] 1.4 Add tests for closed or missing circuit (informs nothing to reset, no writes with or without apply) and for a `running`/reserved chain (blocks and points to `reconcile-static`); observe GREEN

## 2. Orchestrator: guarded, auditable apply

- [x] 2.1 RED tests: apply without `--confirm-stopped` or with empty reason is rejected with no writes; existing `static_writer.lock` blocks without removing it; active or unverifiable writer inventory (injected `process_probe`) blocks
- [x] 2.2 Implement apply under `static_writer_lock`, reusing `active_render_writers`, `_encoded`, `_exclusive` from `interruption_recovery.py` (extract to a shared helper only if needed, without behavior change); observe GREEN
- [x] 2.3 RED tests for publication: `circuit_resets/<reset_id>/record.json` contains operator, date, reason, circuit and per-chain/outcome pre-images with sha256 and next state; old circuit archived as `matlab_process_circuit.<reset_id>.json` byte-identical; new circuit `open:false`, streak 0, empty resolutions, `reset_id`; failed chain → `scheduled` with `circuit_reset_id`; accepted chains and their outcomes byte-identical
- [x] 2.4 Implement publication order per design decision 4 (record → circuit archive → outcome/chain projections → new circuit last); observe GREEN
- [x] 2.5 Interruption tests: inject a failure after each publication step and assert render stays blocked and a repeated apply completes only the same record; repeated apply after completion reports no open circuit and creates nothing new
- [x] 2.6 Accept optional `reset_id` in `BatchCircuit` without changing `schema_version`; test that existing circuit files still load

## 3. Orchestrator: resume integration

- [x] 3.1 Extend outcome verification (`_resolved_outcome` / `archive_attempt` in `pipeline/process_retries.py`) to accept a `circuit_reset_id` projection only when the record exists, hashes match and the chain links the same id; RED test: manually edited circuit + chain without record still blocks
- [x] 3.2 End-to-end test with the fixture from 1.1 and a fake MATLAB runner: after apply, `render_static_scenes` reuses the 4 accepted variants without invoking MATLAB or archiving outputs, dispatches the reprogrammed chain at ordinal 2 with a new UUID, continues the `scheduled` chain, and the summary keeps prior failures visible
- [x] 3.3 Test that a new cutoff after reset reopens the circuit counting only post-reset failures
- [x] 3.4 Add a note to the existing `Corte de procesos MATLAB persistido` error suggesting `reset-circuit <run_root>`; existing message text unchanged (assert in tests)
- [x] 3.5 Regression check: existing forced mode (`resume_if_possible=false` + `overwrite_existing=true`) and `reconcile-static` still refuse/handle an open circuit exactly as before; differential run of `render-static` and `reconcile-static` against the base commit on a scratch copy shows identical stdout/stderr/exit code

## 4. CLI and docs

- [x] 4.1 Add `reset-circuit <run_root>` to `cli.py` with `--apply`, `--confirm-stopped`, `--reason`, mirroring `reconcile-static` output style; CLI tests for read-only output, rejected apply (exit 1, `Reinicio bloqueado: ...`) and successful apply message stating MATLAB was not launched
- [x] 4.2 Update `docs/architecture/contracts.md` (circuit section: explicit reset, eligibility, evidence, publication order) and the CLI help/README usage where `reconcile-static` is documented
- [x] 4.3 Run the full orchestrator suite (`uv run pytest apps/orchestrator/tests`) and `openspec validate add-reset-circuit-command --strict`; both pass (1263 passed; 5 failures pre-existing at base 335a260, reproduced there: 4 RAVEN golden/normalized-config tests and `test_pra_e2e::test_render_static_end_to_end_without_matlab`)

## 5. Operational verification

- [x] 5.1 Run `reset-circuit` read-only on `outputs/directivity_experiment103` and confirm byte-identical `metadata/indexes` and `metadata/process_chains` before/after; plan must list only the failed chain as `scheduled` and the 4 accepted chains as untouched. (Apply on a copy is unsafe: chains store absolute outcome paths pointing to the original batch; a run_root containment guard now blocks that case. Apply on the real batch is left to the operator.)
