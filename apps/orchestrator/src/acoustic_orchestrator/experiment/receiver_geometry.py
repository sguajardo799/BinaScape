import math

from acoustic_orchestrator.config.models import CircularArrayOutputConfig


def build_circular_array_descriptor(
    position_m: list[float], orientation_deg: dict[str, float], output: CircularArrayOutputConfig
) -> dict:
    if output.num_microphones is None or output.radius_m is None:
        raise ValueError("circular_array requiere num_microphones y radius_m")
    count = output.num_microphones
    yaw = orientation_deg["yaw"]
    microphones = []
    for index in range(count):
        angle = math.radians(yaw + 360 * index / count)
        microphones.append(
            {
                "microphone_index": index,
                "channel_index": index + 1,
                "position_m": [
                    position_m[0] + output.radius_m * math.cos(angle),
                    position_m[1],
                    position_m[2] + output.radius_m * math.sin(angle),
                ],
            }
        )
    return {
        "output_type": "circular_array",
        "num_microphones": count,
        "radius_m": output.radius_m,
        "center_m": list(position_m),
        "yaw_deg": yaw,
        "microphones": microphones,
    }
