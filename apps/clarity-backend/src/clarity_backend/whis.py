"""WHIS v302 (+ GCFBv234, MATLAB) hearing-loss processor (design D7, D8).

All WHIS jobs of a manifest run in one ``matlab -batch`` session executing
``matlab/binascape_whis_run.m``. Python prepares one task per non-silent channel under
``<manifest dir>/whis_work/<job_id>/``, watches the result files while MATLAB runs and
finalizes each job as soon as both its channels are available.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from clarity_backend.latency import compensate_latency
from clarity_backend.msbg import OUTPUT_SUBTYPE


WHIS_VERSION = "WHISv302"
GCFB_VERSION = "GCFBv234"
WHIS_BANDS_HZ = (125, 250, 500, 1000, 2000, 4000, 8000)
PROCESSING_SAMPLE_RATE_HZ = 48000
SUPPORTED_INPUT_SAMPLE_RATES_HZ = (44100, 48000)
# Same 1 kHz tone convention as WHIS: RMS -26 dBFS, so its SPL is K - 26.
CALIB_TONE_RMS_DBFS = -26.0
OUT_MID_CRCT = {"free_field": "FreeField", "diffuse_field": "DiffuseField", "itu": "ITU", "eardrum": "EarDrum"}
SYNTH_METHODS = {"dtvf": "DTVF", "fbanasyn": "FBAnaSyn"}
MATLAB_DIR = Path(__file__).resolve().parents[2] / "matlab"
CHANNELS = ("left", "right")
LATENCY_IMPULSE_POSITION = PROCESSING_SAMPLE_RATE_HZ // 4
LATENCY_IMPULSE_AMPLITUDE = 0.5
LATENCY_PROBE_SCALE_DB_SPL = 100.0


# --- Profile mapping -----------------------------------------------------------------------

def map_whis_bands(
    loss_db_by_band: dict[str, float], compression_health: float | dict[str, float] | None, *, ear: str,
) -> tuple[list[float], list[float], list[str]]:
    """Return the 7 WHIS thresholds and compression health values, and the extrapolated bands."""
    if compression_health is None:
        raise ValueError(f"WHIS requiere compression_health en ears.{ear}")
    missing = [str(band) for band in WHIS_BANDS_HZ[1:] if str(band) not in loss_db_by_band]
    if missing:
        raise ValueError(f"WHIS requiere las bandas {', '.join(missing)} en ears.{ear}.loss_db_by_band")
    unsupported = sorted(set(loss_db_by_band) - {str(band) for band in WHIS_BANDS_HZ}, key=float)
    if unsupported:
        raise ValueError(f"WHIS no soporta las bandas {', '.join(unsupported)} en ears.{ear}.loss_db_by_band")
    extrapolated = [] if "125" in loss_db_by_band else ["125"]
    hearing_levels = [float(loss_db_by_band.get(str(band), loss_db_by_band["250"])) for band in WHIS_BANDS_HZ]
    negative = [str(band) for band, level in zip(WHIS_BANDS_HZ, hearing_levels) if level < 0]
    if negative:
        raise ValueError(f"WHIS no admite umbrales negativos (ears.{ear}, bandas {', '.join(negative)})")
    if isinstance(compression_health, dict):
        health = [float(compression_health.get(str(band), compression_health.get("250", 1.0))) for band in WHIS_BANDS_HZ]
    else:
        health = [float(compression_health)] * len(WHIS_BANDS_HZ)
    return hearing_levels, health, extrapolated


# --- Resampling and assembly ---------------------------------------------------------------

def to_processing_rate(channel: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == PROCESSING_SAMPLE_RATE_HZ:
        return np.asarray(channel, dtype=float)
    return resample_poly(np.asarray(channel, dtype=float), 160, 147)


def from_processing_rate(channel: np.ndarray, sample_rate: int, expected_samples: int) -> np.ndarray:
    if sample_rate != PROCESSING_SAMPLE_RATE_HZ:
        channel = resample_poly(np.asarray(channel, dtype=float), 147, 160)
    return compensate_latency(channel, 0, expected_samples)


def validate_input(audio: np.ndarray, sample_rate: int, path: Path) -> None:
    if sample_rate not in SUPPORTED_INPUT_SAMPLE_RATES_HZ:
        raise ValueError(
            f"WHIS no soporta sample_rate {sample_rate} Hz en {path}; "
            f"soportadas: {', '.join(str(rate) for rate in SUPPORTED_INPUT_SAMPLE_RATES_HZ)} Hz"
        )
    if audio.ndim != 2 or audio.shape[1] != 2:
        raise ValueError(f"WHIS requiere WAV estéreo de 2 canales: {path} ({audio.shape[1] if audio.ndim == 2 else 1} canales)")


# --- Session ---------------------------------------------------------------------------------

@dataclass
class PreparedJob:
    job: dict[str, Any]
    work_dir: Path
    input_info: Any
    expected_samples: int
    sample_rate: int
    latency_key: tuple[str, str]
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)


@dataclass
class WhisOutcome:
    job: dict[str, Any]
    status: str  # "completed" | "failed"
    processor_metadata: dict[str, Any] | None = None
    degradation_applied: dict[str, Any] | None = None
    error_type: str = ""
    error_message: str = ""


OutcomeCallback = Callable[[WhisOutcome], None]
Launch = Callable[[list[str], Path], Any]


def run_whis_jobs(
    jobs: list[dict[str, Any]],
    *,
    work_root: Path,
    resolve_profile: Callable[[dict[str, Any]], dict[str, Any]],
    on_outcome: OutcomeCallback,
    launch: Launch | None = None,
    poll_interval_s: float = 0.5,
) -> None:
    """Process WHIS jobs, one MATLAB session per (install_root, matlab_executable)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for job in jobs:
        config = job.get("processor_config") or {}
        key = (str(config.get("install_root", "")), str(config.get("matlab_executable") or "matlab"))
        groups.setdefault(key, []).append(job)
    for (install_root, executable), group in groups.items():
        _run_session(group, Path(install_root), executable, work_root, resolve_profile, on_outcome,
                     launch or _launch_matlab, poll_interval_s)


def _run_session(
    jobs: list[dict[str, Any]],
    install_root: Path,
    executable: str,
    work_root: Path,
    resolve_profile: Callable[[dict[str, Any]], dict[str, Any]],
    on_outcome: OutcomeCallback,
    launch: Launch,
    poll_interval_s: float,
) -> None:
    work_root.mkdir(parents=True, exist_ok=True)
    versions = _install_versions(install_root)
    prepared: list[PreparedJob] = []
    task_paths: list[Path] = []
    latency_probes: dict[tuple[str, str], dict[str, Path]] = {}

    for job in jobs:
        try:
            item = _prepare_job(job, work_root, resolve_profile(job))
        except Exception as exc:  # noqa: BLE001 - every job failure becomes its own failed sidecar
            on_outcome(WhisOutcome(job, "failed", error_type=type(exc).__name__, error_message=str(exc)))
            continue
        if item.latency_key not in latency_probes:
            latency_probes[item.latency_key] = _write_latency_probe(work_root, item.latency_key)
            task_paths.append(latency_probes[item.latency_key]["task"])
        task_paths.extend(item.channels[channel]["task"] for channel in sorted(item.pending))
        prepared.append(item)

    if not prepared:
        return

    task_list = work_root / "tasks.txt"
    task_list.write_text("".join(f"{path.as_posix()}\n" for path in task_paths), encoding="utf-8")
    log_path = work_root / "matlab_session.log"
    command = build_matlab_command(executable, task_list, install_root)
    process = launch(command, log_path)

    latencies: dict[tuple[str, str], int | str] = {}
    remaining = list(prepared)
    while True:
        finished = process.poll() is not None
        _read_latencies(latency_probes, latencies)
        still: list[PreparedJob] = []
        for item in remaining:
            if _collect_channels(item) and item.latency_key in latencies:
                on_outcome(_finalize_job(item, latencies[item.latency_key], versions))
            else:
                still.append(item)
        remaining = still
        if not remaining:
            break
        if finished:
            for item in remaining:
                on_outcome(WhisOutcome(
                    item.job, "failed", error_type="WhisSessionError",
                    error_message=(
                        f"La sesión MATLAB terminó (exit code {process.returncode}) sin resultado para "
                        f"{item.job['job_id']}; ver {log_path.as_posix()}"
                    ),
                ))
            break
        time.sleep(poll_interval_s)
    if process.poll() is None:
        process.wait()


def build_matlab_command(executable: str, task_list: Path, install_root: Path) -> list[str]:
    def quote(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "''")

    resolved = shutil.which(executable) or executable
    statement = (
        f"addpath('{quote(MATLAB_DIR)}'); "
        f"binascape_whis_run('{quote(task_list)}', '{quote(install_root)}')"
    )
    return [resolved, "-batch", statement]


def _launch_matlab(command: list[str], log_path: Path) -> subprocess.Popen:
    log = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)


def _prepare_job(job: dict[str, Any], work_root: Path, profile: dict[str, Any]) -> PreparedJob:
    if job.get("processor") != "whis":
        raise ValueError(f"Procesador no soportado en la ruta WHIS: {job.get('processor')}")
    config = job.get("processor_config") or {}
    synth = SYNTH_METHODS.get(str(config.get("synth_method", "dtvf")))
    if synth is None:
        raise ValueError(f"whis.synth_method no soportado: {config.get('synth_method')}")
    outer = job.get("outer_middle_ear")
    if outer not in OUT_MID_CRCT:
        raise ValueError(f"outer_middle_ear no soportado por WHIS: {outer}")
    calibration = job.get("calibration") or {}
    scale = calibration.get("effective_scale_db_spl")
    if not isinstance(scale, int | float) or not math.isfinite(scale):
        raise ValueError(f"El job {job['job_id']} no trae una escala efectiva K válida (calibration.effective_scale_db_spl)")

    input_path = Path(job["input_wav_path"]).resolve()
    render_metadata = Path(job["input_render_metadata_path"]).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"No existe el WAV de entrada para {job['job_id']}: {input_path}")
    if not render_metadata.is_file():
        raise FileNotFoundError(f"No existe el metadata de render de entrada para {job['job_id']}: {render_metadata}")
    try:
        audio, sample_rate = sf.read(input_path, always_2d=True)
        input_info = sf.info(input_path)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"No se pudo leer el WAV de entrada para WHIS: {input_path}: {exc}") from exc
    validate_input(audio, sample_rate, input_path)

    # Short directory name: job ids are long and Windows paths are limited to 260 characters.
    work_dir = work_root / hashlib.sha1(job["job_id"].encode("utf-8")).hexdigest()[:12]
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    item = PreparedJob(job, work_dir, input_info, audio.shape[0], sample_rate, (outer, synth))
    for index, ear in enumerate(CHANNELS):
        hearing_levels, health, extrapolated = map_whis_bands(
            profile[f"{ear}_loss_db_by_band"], profile.get(f"{ear}_compression_health"), ear=ear,
        )
        signal = to_processing_rate(audio[:, index], sample_rate)
        rms = float(np.sqrt(np.mean(np.square(signal)))) if signal.size else 0.0
        channel: dict[str, Any] = {
            "hearing_levels": hearing_levels, "health": health, "extrapolated": extrapolated,
            "silent": rms == 0.0, "srcsnd_spl_db": None,
        }
        if rms > 0.0:
            channel["srcsnd_spl_db"] = float(scale) + 20 * math.log10(rms)
            paths = _write_task(work_dir, ear, signal, channel["srcsnd_spl_db"], float(scale),
                                hearing_levels, health, synth, OUT_MID_CRCT[outer])
            channel.update(paths)
            item.pending.add(ear)
        item.channels[ear] = channel
    return item


def _write_task(
    work_dir: Path, name: str, signal: np.ndarray, srcsnd_spl_db: float, scale_db: float,
    hearing_levels: list[float], health: list[float], synth: str, out_mid_crct: str,
) -> dict[str, Path]:
    paths = {
        "input": work_dir / f"in_{name}.wav", "output": work_dir / f"out_{name}.wav",
        "result": work_dir / f"result_{name}.json", "error": work_dir / f"error_{name}.json",
        "task": work_dir / f"task_{name}.json",
    }
    sf.write(paths["input"], signal, PROCESSING_SAMPLE_RATE_HZ, subtype="FLOAT")
    paths["task"].write_text(json.dumps({
        "input_wav": paths["input"].resolve().as_posix(),
        "output_wav": paths["output"].resolve().as_posix(),
        "result_json": paths["result"].resolve().as_posix(),
        "error_json": paths["error"].resolve().as_posix(),
        "srcsnd_spl_db": srcsnd_spl_db,
        "calib_tone_spl_db": scale_db + CALIB_TONE_RMS_DBFS,
        "hearing_level_db": hearing_levels,
        "compression_health": health,
        "synth_method": synth,
        "out_mid_crct": out_mid_crct,
    }), encoding="utf-8")
    return paths


def _write_latency_probe(work_root: Path, key: tuple[str, str]) -> dict[str, Path]:
    """Impulse through a 0 dB HL, healthy-compression ear for this configuration (design D10)."""
    outer, synth = key
    probe_dir = work_root / f"lat_{outer}_{synth}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    impulse = np.zeros(PROCESSING_SAMPLE_RATE_HZ)
    impulse[LATENCY_IMPULSE_POSITION] = LATENCY_IMPULSE_AMPLITUDE
    rms = float(np.sqrt(np.mean(impulse**2)))
    return _write_task(
        probe_dir, "impulse", impulse, LATENCY_PROBE_SCALE_DB_SPL + 20 * math.log10(rms), LATENCY_PROBE_SCALE_DB_SPL,
        [0.0] * len(WHIS_BANDS_HZ), [1.0] * len(WHIS_BANDS_HZ), synth, OUT_MID_CRCT[outer],
    )


def _read_latencies(probes: dict[tuple[str, str], dict[str, Path]], latencies: dict[tuple[str, str], int | str]) -> None:
    for key, paths in probes.items():
        if key in latencies:
            continue
        if paths["error"].is_file():
            latencies[key] = f"medición de latencia WHIS fallida: {_read_json(paths['error']).get('message', '')}"
        elif paths["result"].is_file() and paths["output"].is_file():
            output, _ = sf.read(paths["output"])
            latencies[key] = int(np.argmax(np.abs(output))) - LATENCY_IMPULSE_POSITION


def _collect_channels(item: PreparedJob) -> bool:
    """True when every non-silent channel of the job has a result or an error."""
    for ear in list(item.pending):
        paths = item.channels[ear]
        if paths["error"].is_file():
            item.channels[ear]["error_payload"] = _read_json(paths["error"])
            item.pending.discard(ear)
        elif paths["result"].is_file() and paths["output"].is_file():
            item.channels[ear]["result_payload"] = _read_json(paths["result"])
            item.pending.discard(ear)
    return not item.pending


def _finalize_job(item: PreparedJob, latency: int | str, versions: dict[str, Any]) -> WhisOutcome:
    job = item.job
    errors = [f"{ear}: {item.channels[ear]['error_payload'].get('message', '')}"
              for ear in CHANNELS if "error_payload" in item.channels[ear]]
    if errors:
        return WhisOutcome(job, "failed", error_type="WhisMatlabError", error_message="; ".join(errors))
    if isinstance(latency, str):
        return WhisOutcome(job, "failed", error_type="WhisLatencyError", error_message=latency)
    try:
        channels = []
        for ear in CHANNELS:
            channel = item.channels[ear]
            if channel["silent"]:
                channels.append(np.zeros(item.expected_samples))
                continue
            processed, _ = sf.read(channel["output"])
            expected_48k = to_processing_rate(np.zeros(item.expected_samples), item.sample_rate).shape[0]
            aligned = compensate_latency(processed, latency, expected_48k)
            channels.append(from_processing_rate(aligned, item.sample_rate, item.expected_samples))
        audio = np.column_stack(channels)
        output_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        output_path = Path(job["expected_output_wav_path"]).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # float32: values above full scale are kept and reported, never clipped (design D13).
        sf.write(output_path, audio, item.sample_rate, format="WAV", subtype=OUTPUT_SUBTYPE)
    except Exception as exc:  # noqa: BLE001
        return WhisOutcome(job, "failed", error_type=type(exc).__name__, error_message=str(exc))

    results = {ear: item.channels[ear].get("result_payload") or {} for ear in CHANNELS}
    first = next((result for result in results.values() if result), {})
    config = job["processor_config"]
    processor = {
        "name": "whis",
        "whis_version": WHIS_VERSION,
        "whis_function": first.get("whis_version"),
        "gcfb_version": GCFB_VERSION,
        **versions,
        "matlab_release": first.get("matlab_release"),
        "synth_method": config.get("synth_method", "dtvf"),
        "outer_middle_ear": job["outer_middle_ear"],
        "out_mid_crct": OUT_MID_CRCT[job["outer_middle_ear"]],
        "sample_rate_hz": item.sample_rate,
        "processing_sample_rate_hz": PROCESSING_SAMPLE_RATE_HZ,
        "resampled": item.sample_rate != PROCESSING_SAMPLE_RATE_HZ,
        "channels": 2,
        "latency_compensated": True,
        "latency_compensation_samples": latency,
        "latency_sample_rate_hz": PROCESSING_SAMPLE_RATE_HZ,
        "output_peak": output_peak,
        "output_subtype": OUTPUT_SUBTYPE,
        "channel_levels": {
            ear: {"silent": item.channels[ear]["silent"], "srcsnd_spl_db": item.channels[ear]["srcsnd_spl_db"]}
            for ear in CHANNELS
        },
    }
    degradation = {ear: _degradation_for_ear(item.channels[ear], results[ear]) for ear in CHANNELS}
    shutil.rmtree(item.work_dir, ignore_errors=True)
    return WhisOutcome(job, "completed", processor_metadata=processor, degradation_applied=degradation)


def _degradation_for_ear(channel: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    bands = [str(band) for band in WHIS_BANDS_HZ]

    def by_band(values: Any) -> dict[str, float] | None:
        if values is None:
            return None
        values = values if isinstance(values, list) else [values]
        return {band: float(value) for band, value in zip(bands, values)}

    return {
        "loss_db_by_band": dict(zip(bands, channel["hearing_levels"])),
        "audiogram_frequencies_hz": list(WHIS_BANDS_HZ),
        "extrapolated_bands": channel["extrapolated"],
        "compression_health_requested": dict(zip(bands, channel["health"])),
        "compression_health_applied": by_band(result.get("compression_health_applied")),
        "pin_loss_db_act": by_band(result.get("pin_loss_db_act")),
        "pin_loss_db_pas": by_band(result.get("pin_loss_db_pas")),
    }


def _install_versions(install_root: Path) -> dict[str, Any]:
    return {
        "install_root": install_root.resolve().as_posix(),
        "whis_commit": _git_commit(install_root / "WHIS"),
        "gcfb_commit": _git_commit(install_root / "gammachirp-filterbank"),
    }


def _git_commit(path: Path) -> str | None:
    if not (path / ".git").exists() or shutil.which("git") is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"message": f"resultado ilegible {path.name}: {exc}"}
    return payload if isinstance(payload, dict) else {"message": f"resultado inválido {path.name}"}
