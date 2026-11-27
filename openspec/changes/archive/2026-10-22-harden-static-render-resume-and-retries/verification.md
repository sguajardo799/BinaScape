# Verification

## Automated checks

- Python full regression: **910 passed** in 381.85 seconds.
  Command: `.\.venv\Scripts\python.exe -m pytest apps/orchestrator/tests -q --disable-warnings --maxfail=1`.
- Additional final regressions: explicit fresh execution of a pending chain and interrupted partial-lot restart, **2 passed**. Command: `.\.venv\Scripts\python.exe -m pytest apps/orchestrator/tests/test_render_resilience.py -q -k "explicit_fresh or interrupted_partial_lot"`.
- Safety regression: **6 passed** in `test_static_resume_safety.py`, covering protected secondary variants, persisted T30 candidates, changed crop PCM, terminal-chain preflight and draining workers after persistence failure.
- `.\.venv\Scripts\python.exe -m ruff check apps/orchestrator`: passed.
- `.\.venv\Scripts\python.exe -m mypy --config-file apps/orchestrator/pyproject.toml apps/orchestrator/src`: passed, 32 source files. Running without the project config reports missing PyYAML stubs; the configured override is applied by the command above. No dependencies were installed to bypass this.
- `openspec validate harden-static-render-resume-and-retries --strict`: passed.
- `git diff --check`: passed; Git reports existing Windows LF/CRLF normalization warnings, not whitespace errors.

## MATLAB

MATLAB R2025b: **130 passed, 0 failed, 0 incomplete**, across all 22 `apps/matlab/tests/test_*.m` files. The suite was built with `testsuite`, executed with `run`, and checked with `assertSuccess`. This includes static validation, metadata, diagnostics, completion and failure reporting. The failure-report subset has 7 passing tests.

`TEMP`, `TMP`, MATLAB working directory, log and saved results were directed to `C:/Users/sguaj/AppData/Local/Temp/opencode`. Evidence: `task6_2_matlab_full_suite.log` and `task6_2_matlab_results.mat` in that directory. No `verify_*` real-render fixtures or RAVEN experiment renders were executed.

## Existing experiment: read-only observations

- Loaded and structurally validated all 1,000 existing scene manifests using the new static-manifest validator.
- Read-only artifact preflight passed for completed scene `directivity2_0001` and failed scene `directivity2_0476`.
- Full deterministic legacy-plan replay was attempted in inspection-only mode and exceeded a 600-second command limit. Full legacy adoption of this real batch is therefore **not certified by this verification**. The synthetic legacy/restart tests are separate evidence, not a claim that this timed-out check passed.
- None of these observations called plan application, created a writer lock, launched a renderer or published experiment artifacts. No changes appear under `outputs/` in the Git diff.
- The original MATLAB error remains of unconfirmed internal cause; structural input validation does not prove RAVEN/DAFF/MAT interoperability or guarantee that a retry will succeed.

## Existing work

The workspace was already dirty with process/render-acceptance work before implementation. Those changes were extended in place, not reset or reverted. Verification concerns the resulting working tree; Git diff totals are not attributed solely to this change.
