from pathlib import Path
from dataclasses import dataclass
import os
import tempfile
import wave


_CHUNK_FRAMES = 16384


class PreparedAudioConflict(ValueError):
    """An existing prepared asset does not match the expected PCM source."""


@dataclass(frozen=True)
class PreparedSourceInspection:
    rewrites: dict[str, str]
    missing_paths: tuple[str, ...]


def inspect_scene_source_assets(scene_manifest: dict, prepared_audio_dir: Path) -> PreparedSourceInspection:
    """Resolve expected paths without writes; missing is allowed, corruption is not."""
    target_duration_s = scene_manifest.get("render", {}).get("target_duration_s")
    if target_duration_s is None:
        return PreparedSourceInspection({}, ())

    rewrites: dict[str, str] = {}
    missing: list[str] = []
    for source in scene_manifest["sources"]:
        prepared_path = prepare_trimmed_wav_copy(
            source_id=source["source_id"],
            audio_path=Path(source["audio_path"]),
            total_duration_s=target_duration_s,
            prepared_audio_dir=prepared_audio_dir,
            create=False,
        )
        if prepared_path is not None:
            rewrites[source["source_id"]] = prepared_path
            if not Path(prepared_path).exists():
                missing.append(prepared_path)

    return PreparedSourceInspection(rewrites, tuple(missing))


def prepare_scene_source_assets(
    scene_manifest: dict, prepared_audio_dir: Path, *, create: bool = True
) -> dict[str, str]:
    """Validate existing assets, optionally creating missing ones after preflight.

    With create=False, returns the same runtime rewrites even for absent assets.
    Call inspect_scene_source_assets to distinguish missing assets from valid ones.
    Neither mode silently replaces an incompatible existing asset.
    """
    inspection = inspect_scene_source_assets(scene_manifest, prepared_audio_dir)
    if create:
        for source in scene_manifest.get("sources", []):
            if source["source_id"] in inspection.rewrites:
                prepare_trimmed_wav_copy(
                    source_id=source["source_id"],
                    audio_path=Path(source["audio_path"]),
                    total_duration_s=scene_manifest["render"]["target_duration_s"],
                    prepared_audio_dir=prepared_audio_dir,
                )

    return inspection.rewrites


def prepare_trimmed_wav_copy(
    *,
    source_id: str,
    audio_path: Path,
    total_duration_s: float,
    prepared_audio_dir: Path,
    create: bool = True,
) -> str | None:
    audio_path = audio_path.resolve()
    with wave.open(str(audio_path), "rb") as source_wav:
        frame_rate = source_wav.getframerate()
        total_frames = source_wav.getnframes()
        trim_frames = min(total_frames, int(total_duration_s * frame_rate))

        if trim_frames >= total_frames:
            return None

        params = source_wav.getparams()
        prepared_path = (prepared_audio_dir / f"{source_id}__trim_{_duration_token(total_duration_s)}.wav").resolve()
        if prepared_path.exists():
            _validate_prepared_wav(prepared_path, source_wav, trim_frames)
        elif create:
            prepared_path.parent.mkdir(parents=True, exist_ok=True)
            # A complete same-filesystem temporary file is linked into place.
            # Unlike replace/rename, link never clobbers a concurrent creator.
            with tempfile.NamedTemporaryFile(dir=prepared_path.parent, suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
            try:
                with wave.open(str(temp_path), "wb") as prepared_wav:
                    prepared_wav.setparams(params._replace(nframes=trim_frames))
                    remaining = trim_frames
                    while remaining:
                        count = min(remaining, _CHUNK_FRAMES)
                        frames = source_wav.readframes(count)
                        if len(frames) != count * params.nchannels * params.sampwidth:
                            raise ValueError(f"Truncated source WAV: {audio_path}")
                        prepared_wav.writeframesraw(frames)
                        remaining -= count
                with temp_path.open("r+b") as completed:
                    os.fsync(completed.fileno())
                try:
                    os.link(temp_path, prepared_path)
                except FileExistsError:
                    source_wav.rewind()
                    _validate_prepared_wav(prepared_path, source_wav, trim_frames)
            finally:
                temp_path.unlink(missing_ok=True)

    return prepared_path.as_posix()


def _validate_prepared_wav(prepared_path: Path, source_wav: wave.Wave_read, trim_frames: int) -> None:
    try:
        with wave.open(str(prepared_path), "rb") as prepared_wav:
            expected = (
                source_wav.getnchannels(), source_wav.getsampwidth(),
                source_wav.getframerate(), trim_frames, source_wav.getcomptype(),
            )
            actual = (
                prepared_wav.getnchannels(), prepared_wav.getsampwidth(),
                prepared_wav.getframerate(), prepared_wav.getnframes(), prepared_wav.getcomptype(),
            )
            if actual != expected:
                raise ValueError("PCM dimensions differ")
            remaining = trim_frames
            while remaining:
                count = min(remaining, _CHUNK_FRAMES)
                frames = source_wav.readframes(count)
                if len(frames) != count * expected[0] * expected[1]:
                    raise ValueError("source PCM payload is truncated")
                if prepared_wav.readframes(count) != frames:
                    raise ValueError("PCM payload differs or is truncated")
                remaining -= count
    except (OSError, wave.Error, EOFError, ValueError) as exc:
        raise PreparedAudioConflict(f"Prepared audio conflict at {prepared_path}: {exc}") from exc


def _duration_token(total_duration_s: float) -> str:
    return f"{total_duration_s:.6f}".rstrip("0").rstrip(".").replace(".", "p")
