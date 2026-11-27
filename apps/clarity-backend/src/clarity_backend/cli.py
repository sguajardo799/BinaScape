import argparse
import json
import math
from pathlib import Path
from time import monotonic

import yaml

from clarity_backend import msbg, whis
from clarity_backend.progress import ClarityProgress, JobCallback, JobEvent


SCHEMA_VERSION = "1.0"


def main() -> None:
    parser = argparse.ArgumentParser(prog="clarity-backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_manifest_parser = subparsers.add_parser("run-manifest", help="Procesa un manifiesto JSONL de jobs de Clarity")
    run_manifest_parser.add_argument("manifest_path", type=Path)
    run_manifest_parser.add_argument("--no-progress", action="store_true", help="Desactiva el progreso en stderr")

    args = parser.parse_args()
    if args.command == "run-manifest":
        with ClarityProgress(enabled=not args.no_progress) as progress:
            run_manifest(args.manifest_path, progress=progress if not args.no_progress else None)


def run_manifest(manifest_path: Path, *, progress: JobCallback | None = None) -> None:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe el manifiesto de Clarity: {manifest_path}")

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    total = sum(bool(line.strip()) for line in lines)
    failed_jobs = completed_jobs = 0
    started = monotonic()

    def emit(kind: str, **kwargs) -> None:
        nonlocal progress
        if progress is not None:
            try:
                progress(JobEvent(kind, total, completed=completed_jobs, failed=failed_jobs,
                                  elapsed=max(0.0, monotonic() - started), **kwargs))
            except Exception:
                progress = None

    emit("begin")
    interrupted = True
    whis_jobs: list[dict] = []
    try:
        for raw_line in lines:
            if not raw_line.strip():
                continue
            job = json.loads(raw_line)
            if isinstance(job, dict) and job.get("processor") == "whis":
                whis_jobs.append(job)  # processed together after the MSBG jobs
                continue
            job_started = monotonic()
            key = str(job.get("job_id", "")) if isinstance(job, dict) else ""
            emit("start", key=key)
            succeeded = _process_job(job)
            if succeeded:
                completed_jobs += 1
            else:
                failed_jobs += 1
            emit("resolved", key=key, duration=max(0.0, monotonic() - job_started),
                 status="completed" if succeeded else "failed")

        if whis_jobs:
            # All WHIS jobs of the manifest share one MATLAB session (design D7).
            last_resolved = monotonic()

            def record_outcome(outcome: whis.WhisOutcome) -> None:
                nonlocal completed_jobs, failed_jobs, last_resolved
                if outcome.status == "completed":
                    write_completed_result(
                        outcome.job,
                        processor_metadata=outcome.processor_metadata or {},
                        degradation_applied=outcome.degradation_applied or {},
                        ignored_fields={"left": [], "right": []},
                    )
                    completed_jobs += 1
                else:
                    write_failed_result(outcome.job, outcome.error_type, outcome.error_message)
                    failed_jobs += 1
                now = monotonic()
                emit("resolved", key=str(outcome.job.get("job_id", "")), duration=max(0.0, now - last_resolved),
                     status=outcome.status)
                last_resolved = now

            emit("start", key=f"WHIS ({len(whis_jobs)} jobs, sesión MATLAB)")
            whis.run_whis_jobs(
                whis_jobs,
                work_root=manifest_path.parent / "whis_work",
                resolve_profile=lambda job: _resolve_hearing_profile(
                    Path(job["hearing_profiles_path"]).resolve(), job["hearing_profile_id"],
                ),
                on_outcome=record_outcome,
            )
        interrupted = False
    finally:
        emit("finish", status="interrumpido" if interrupted else "con fallos" if failed_jobs else "completo")

    if failed_jobs:
        raise SystemExit(1)


def _process_job(job: dict) -> bool:
    output_dir = Path(job["output_dir"]).resolve()
    output_wav_path = Path(job["expected_output_wav_path"]).resolve()
    metadata_path = Path(job["expected_output_metadata_path"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        input_wav_path = Path(job["input_wav_path"]).resolve()
        input_render_metadata_path = Path(job["input_render_metadata_path"]).resolve()
        hearing_profiles_path = Path(job["hearing_profiles_path"]).resolve()

        if not input_wav_path.is_file():
            raise FileNotFoundError(f"No existe el WAV de entrada para {job['job_id']}: {input_wav_path}")

        if not input_render_metadata_path.is_file():
            raise FileNotFoundError(
                f"No existe el metadata de render de entrada para {job['job_id']}: {input_render_metadata_path}"
            )

        if job["processor"] != "msbg":
            raise ValueError(f"Procesador no soportado en la ruta MSBG: {job['processor']}")
        scale = _job_effective_scale(job)
        level_handling = str(job["processor_config"].get("level_handling", "true_level"))
        hearing_profile = _resolve_hearing_profile(hearing_profiles_path, job["hearing_profile_id"])
        left_audiogram, right_audiogram, degradation_applied = msbg.build_audiograms(hearing_profile)
        processor_metadata = msbg.process_wav(
            input_wav_path,
            output_wav_path,
            left_audiogram,
            right_audiogram,
            effective_scale_db_spl=scale,
            outer_middle_ear=job["outer_middle_ear"],
            level_handling=level_handling,
        )
        write_completed_result(
            job,
            processor_metadata=processor_metadata,
            degradation_applied=degradation_applied,
            ignored_fields=msbg.ignored_profile_fields(hearing_profile),
            calibration_extra={"level_handling": level_handling},
        )
        return True
    except Exception as exc:
        write_failed_result(job, type(exc).__name__, str(exc))
        return False


def write_completed_result(
    job: dict,
    *,
    processor_metadata: dict[str, object],
    degradation_applied: dict[str, object],
    ignored_fields: dict[str, list[str]],
    calibration_extra: dict[str, object] | None = None,
) -> None:
    """Completed sidecar: identity, the render calibration K the job carried, signature and processor."""
    _write_result_metadata(
        Path(job["expected_output_metadata_path"]).resolve(),
        {
            **_job_identity_payload(job),
            "hearing_profiles_path": Path(job["hearing_profiles_path"]).resolve().as_posix(),
            "input_wav_path": Path(job["input_wav_path"]).resolve().as_posix(),
            "input_render_metadata_path": Path(job["input_render_metadata_path"]).resolve().as_posix(),
            "output_wav_path": Path(job["expected_output_wav_path"]).resolve().as_posix(),
            "status": "completed",
            "processor": processor_metadata,
            "degradation_applied": degradation_applied,
            "calibration": {**job["calibration"], **(calibration_extra or {})},
            "outer_middle_ear": job["outer_middle_ear"],
            "processing_signature": job["processing_signature"],
            "ignored_profile_fields": ignored_fields,
        },
    )


def write_failed_result(job: dict, error_type: str, message: str) -> None:
    metadata_path = Path(job["expected_output_metadata_path"]).resolve()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    _write_result_metadata(
        metadata_path,
        {
            **_job_identity_payload(job),
            "hearing_profiles_path": Path(job["hearing_profiles_path"]).resolve().as_posix(),
            "input_wav_path": Path(job["input_wav_path"]).resolve().as_posix(),
            "input_render_metadata_path": Path(job["input_render_metadata_path"]).resolve().as_posix(),
            "output_wav_path": None,
            "status": "failed",
            "processor": {"name": job.get("processor")},
            "processing_signature": job.get("processing_signature"),
            "error": {
                "type": error_type,
                "message": message,
            },
        },
    )


def _job_effective_scale(job: dict) -> float:
    """K is computed by the orchestrator once per render; the backend never recomputes it."""
    calibration = job.get("calibration")
    scale = calibration.get("effective_scale_db_spl") if isinstance(calibration, dict) else None
    if not isinstance(scale, int | float) or not math.isfinite(scale):
        raise ValueError(f"El job {job['job_id']} no trae una escala efectiva K válida (calibration.effective_scale_db_spl)")
    return float(scale)


def _job_identity_payload(job: dict) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job["job_id"],
        "variant_id": job["variant_id"],
        "scene_id": job["scene_id"],
        "output_type": job["output_type"],
        "hearing_profile_id": job["hearing_profile_id"],
    }


def _resolve_hearing_profile(hearing_profiles_path: Path, hearing_profile_id: str) -> dict[str, object]:
    catalog = _load_hearing_profiles_catalog(hearing_profiles_path)
    try:
        return catalog[hearing_profile_id]
    except KeyError as exc:
        raise ValueError(f"No existe hearing_profile_id '{hearing_profile_id}' en {hearing_profiles_path}") from exc


def _load_hearing_profiles_catalog(hearing_profiles_path: Path) -> dict[str, dict[str, object]]:
    if not hearing_profiles_path.is_file():
        raise FileNotFoundError(f"No existe el catálogo de perfiles auditivos: {hearing_profiles_path}")

    try:
        raw_catalog = yaml.safe_load(hearing_profiles_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"No se pudo leer el catálogo de perfiles auditivos: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"El catálogo de perfiles auditivos no es YAML válido: {exc}") from exc

    if not isinstance(raw_catalog, dict):
        raise ValueError("El catálogo de perfiles auditivos debe contener un objeto raíz")

    raw_profiles = raw_catalog.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("El catálogo de perfiles auditivos debe contener una lista no vacía en profiles")

    catalog: dict[str, dict[str, object]] = {}
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, dict):
            raise ValueError("Cada perfil auditivo en profiles debe ser un objeto")

        hearing_profile_id = str(raw_profile.get("hearing_profile_id", "")).strip()
        if hearing_profile_id == "":
            raise ValueError("Cada perfil auditivo debe contener hearing_profile_id no vacío")
        if hearing_profile_id in catalog:
            raise ValueError(f"hearing_profile_id duplicado en {hearing_profiles_path}: {hearing_profile_id}")

        left_loss_db_by_band = _parse_loss_db_by_band(raw_profile, hearing_profile_id, "left")
        right_loss_db_by_band = _parse_loss_db_by_band(raw_profile, hearing_profile_id, "right")
        catalog[hearing_profile_id] = {
            "hearing_profile_id": hearing_profile_id,
            "left_loss_db_by_band": left_loss_db_by_band,
            "right_loss_db_by_band": right_loss_db_by_band,
            "left_compression_health": _parse_compression_health(
                raw_profile, hearing_profile_id, "left", left_loss_db_by_band,
            ),
            "right_compression_health": _parse_compression_health(
                raw_profile, hearing_profile_id, "right", right_loss_db_by_band,
            ),
        }

    return catalog


def _parse_loss_db_by_band(raw_profile: dict, hearing_profile_id: str, ear_name: str) -> dict[str, float]:
    ears = raw_profile.get("ears")
    if not isinstance(ears, dict):
        raise ValueError(f"El perfil {hearing_profile_id} debe contener ears")

    ear = ears.get(ear_name)
    if not isinstance(ear, dict):
        raise ValueError(f"El perfil {hearing_profile_id} debe contener ears.{ear_name}")

    loss_db_by_band = ear.get("loss_db_by_band")
    if not isinstance(loss_db_by_band, dict) or not loss_db_by_band:
        raise ValueError(f"El perfil {hearing_profile_id} debe contener ears.{ear_name}.loss_db_by_band no vacío")

    normalized_loss_db_by_band: dict[str, float] = {}
    for band_name, loss_db in loss_db_by_band.items():
        normalized_band_name = str(band_name).strip()
        if normalized_band_name == "":
            raise ValueError(f"El perfil {hearing_profile_id} contiene una banda vacía en {ear_name}")
        if not isinstance(loss_db, int | float) or not math.isfinite(loss_db):
            raise ValueError(
                f"El perfil {hearing_profile_id} debe contener valores numéricos finitos en ears.{ear_name}.loss_db_by_band"
            )
        normalized_loss_db_by_band[normalized_band_name] = float(loss_db)

    return normalized_loss_db_by_band


def _parse_compression_health(
    raw_profile: dict, hearing_profile_id: str, ear_name: str, loss_db_by_band: dict[str, float],
) -> float | dict[str, float] | None:
    """Optional WHIS compression health: a number in [0, 1] or one per loss_db_by_band band."""
    raw = raw_profile["ears"][ear_name].get("compression_health")
    if raw is None:
        return None
    field = f"El perfil {hearing_profile_id} debe contener en ears.{ear_name}.compression_health"

    def _value(value: object, band: str | None) -> float:
        where = f" (banda {band})" if band else ""
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{field} valores finitos en [0, 1]{where}")
        return float(value)

    if not isinstance(raw, dict):
        return _value(raw, None)
    per_band = {str(band).strip(): _value(value, str(band).strip()) for band, value in raw.items()}
    if set(per_band) != set(loss_db_by_band):
        raise ValueError(f"{field} las mismas bandas que loss_db_by_band")
    return per_band


def _write_result_metadata(metadata_path: Path, payload: dict[str, object]) -> None:
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
