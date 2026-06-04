import argparse
import json
import math
from pathlib import Path

import yaml

from clarity_backend import msbg


SCHEMA_VERSION = "1.0"


def main() -> None:
    parser = argparse.ArgumentParser(prog="clarity-backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_manifest_parser = subparsers.add_parser("run-manifest", help="Procesa un manifiesto JSONL de jobs de Clarity")
    run_manifest_parser.add_argument("manifest_path", type=Path)

    args = parser.parse_args()
    if args.command == "run-manifest":
        run_manifest(args.manifest_path)


def run_manifest(manifest_path: Path) -> None:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No existe el manifiesto de Clarity: {manifest_path}")

    failed_jobs = 0
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        job = json.loads(raw_line)
        if not _process_job(job):
            failed_jobs += 1

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

        hearing_profile = _resolve_hearing_profile(hearing_profiles_path, job["hearing_profile_id"])
        left_audiogram, right_audiogram, degradation_applied = msbg.build_audiograms(hearing_profile)
        processor_metadata = msbg.process_wav(
            input_wav_path,
            output_wav_path,
            left_audiogram,
            right_audiogram,
        )
        _write_result_metadata(
            metadata_path,
            {
                **_job_identity_payload(job),
                "hearing_profiles_path": hearing_profiles_path.as_posix(),
                "input_wav_path": input_wav_path.as_posix(),
                "input_render_metadata_path": input_render_metadata_path.as_posix(),
                "output_wav_path": output_wav_path.as_posix(),
                "status": "completed",
                "processor": processor_metadata,
                "degradation_applied": degradation_applied,
            },
        )
        return True
    except Exception as exc:
        _write_result_metadata(
            metadata_path,
            {
                **_job_identity_payload(job),
                "hearing_profiles_path": Path(job["hearing_profiles_path"]).resolve().as_posix(),
                "input_wav_path": Path(job["input_wav_path"]).resolve().as_posix(),
                "input_render_metadata_path": Path(job["input_render_metadata_path"]).resolve().as_posix(),
                "output_wav_path": None,
                "status": "failed",
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            },
        )
        return False


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

        catalog[hearing_profile_id] = {
            "hearing_profile_id": hearing_profile_id,
            "left_loss_db_by_band": _parse_loss_db_by_band(raw_profile, hearing_profile_id, "left"),
            "right_loss_db_by_band": _parse_loss_db_by_band(raw_profile, hearing_profile_id, "right"),
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


def _write_result_metadata(metadata_path: Path, payload: dict[str, object]) -> None:
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
