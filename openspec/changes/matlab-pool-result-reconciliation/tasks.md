# Tasks

## 1. Tests

- [x] 1.1 Reproduce experiment 111 end to end in `tests/test_pool_result_recovery.py`: a persistently locked pool result aborts the run and blocks resume with `termination was not observed` (RED observed before implementation)
- [x] 1.2 Cover reconcile → resume without re-rendering, read-only inspection, preimages and idempotency, required confirmations, missing or contradictory evidence, active writers, an ineligible accepted attempt, a forged outcome and the CLI

## 2. Implementation

- [x] 2.1 Add `pipeline/pool_result_recovery.py` with `reconcile_pool_result` and `verify_pool_result_resolution`, reusing the interruption-recovery helpers and guards
- [x] 2.2 Verify reconciled outcomes in `process_retries._resolved_outcome`
- [x] 2.3 Add the `reconcile-pool-result` CLI command

## 3. Docs

- [x] 3.1 Document the command and its evidence next to the pool queue contract in `docs/architecture/contracts.md`
