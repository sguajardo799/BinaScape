from pathlib import Path
import wave


def prepare_scene_source_assets(scene_manifest: dict, prepared_audio_dir: Path) -> dict[str, str]:
    target_duration_s = scene_manifest.get("render", {}).get("target_duration_s")
    if target_duration_s is None:
        return {}

    rewrites: dict[str, str] = {}
    for source in scene_manifest["sources"]:
        prepared_path = prepare_trimmed_wav_copy(
            source_id=source["source_id"],
            audio_path=Path(source["audio_path"]),
            total_duration_s=target_duration_s,
            prepared_audio_dir=prepared_audio_dir,
        )
        if prepared_path is not None:
            rewrites[source["source_id"]] = prepared_path

    return rewrites


def prepare_trimmed_wav_copy(
    *,
    source_id: str,
    audio_path: Path,
    total_duration_s: float,
    prepared_audio_dir: Path,
) -> str | None:
    audio_path = audio_path.resolve()
    with wave.open(str(audio_path), "rb") as source_wav:
        frame_rate = source_wav.getframerate()
        total_frames = source_wav.getnframes()
        trim_frames = min(total_frames, int(total_duration_s * frame_rate))

        if trim_frames >= total_frames:
            return None

        params = source_wav.getparams()
        trimmed_frames = source_wav.readframes(trim_frames)

    prepared_audio_dir.mkdir(parents=True, exist_ok=True)
    prepared_path = (prepared_audio_dir / f"{source_id}__trim_{_duration_token(total_duration_s)}.wav").resolve()
    with wave.open(str(prepared_path), "wb") as prepared_wav:
        prepared_wav.setnchannels(params.nchannels)
        prepared_wav.setsampwidth(params.sampwidth)
        prepared_wav.setframerate(params.framerate)
        prepared_wav.writeframes(trimmed_frames)

    return prepared_path.as_posix()


def _duration_token(total_duration_s: float) -> str:
    return f"{total_duration_s:.6f}".rstrip("0").rstrip(".").replace(".", "p")
