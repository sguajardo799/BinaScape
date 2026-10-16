def is_clarity_output_eligible(output_type: str) -> bool:
    if "__" in output_type:
        base, suffix = output_type.rsplit("__", 1)
        if suffix.isdecimal():
            output_type = base
    return output_type == "binaural_hrtf"
