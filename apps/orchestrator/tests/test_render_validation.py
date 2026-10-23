import pytest
import json
import struct
import wave

from acoustic_orchestrator.pipeline.render_validation import (
    RenderValidation, read_render_metadata, requires_metadata, validate_dimensions,
    RAVEN_OCTAVE_FREQUENCIES_HZ, validate_noise, validate_receiver_and_seed, validate_sources, validate_t30, validate_wav,
)


@pytest.mark.parametrize("profile,configured,canonical,required", [
    ("normal", False, False, False), ("normal", True, False, True),
    ("normal", False, True, True), ("recovery", False, False, True),
    ("reuse", False, False, False), ("reuse", False, True, True),
])
def test_metadata_profiles(profile, configured, canonical, required):
    assert requires_metadata(metadata_required=configured, canonical=canonical, profile=profile) is required


def test_unknown_validation_profile_is_not_silently_normal():
    with pytest.raises(ValueError, match="unknown render validation profile"):
        requires_metadata(metadata_required=False, canonical=False, profile="unknown")


@pytest.mark.parametrize("field,validity", [
    ("missing", "incomplete"), ("invalid", "invalid"), ("unverifiable", "not_verifiable"),
])
def test_validation_preserves_failure_categories(field, validity):
    result = RenderValidation(**{field: ["test_reason"]})
    assert result.validity == validity
    assert not result.artifacts_ok
    assert result.as_record()["issues"] == ["test_reason"]
    assert not result.as_record()["ok"]


def test_acoustic_rejection_is_not_artifact_corruption():
    result = RenderValidation(acoustic_rejection={"reason": "raven_t30_non_finite_or_non_positive"})
    assert result.artifacts_ok
    assert result.validity == "invalid"
    assert result.as_record()["artifact_issues"] == []
    assert not result.as_record()["ok"]


def test_valid_result_has_no_issues():
    result = RenderValidation()
    assert result.artifacts_ok
    assert result.validity == "valid"
    assert result.as_record() == {
        "ok": True, "validity": "valid", "issues": [], "artifact_issues": [], "acoustic_rejection": None,
    }


def write_wav(path, *, channels=2, samples=200, rate=44100, width=2):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(bytes(samples * channels * width))
    return path


@pytest.mark.parametrize("channels", [2, 4, 8])
@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_full_pcm_payload_is_valid_for_expected_receiver(tmp_path, channels, width):
    path = write_wav(tmp_path / "out.wav", channels=channels, width=width, samples=70000)
    result = validate_wav(path, expected_channels=channels)
    assert result.validity == "valid"
    assert result.wav_info == {"num_channels": channels, "num_samples": 70000,
                               "sample_rate_hz": 44100, "sample_width_bytes": width}


def test_pcm_silence_is_not_an_integrity_error(tmp_path):
    assert validate_wav(write_wav(tmp_path / "silence.wav"), expected_channels=2).artifacts_ok


@pytest.mark.parametrize("case,reason", [
    ("text", "wav_unreadable"), ("truncated", "wav_unreadable"),
    ("empty", "wav_empty"), ("channels", "wav_channel_count_mismatch"),
    ("rate", "wav_sample_rate_mismatch"), ("unaligned", "wav_frame_alignment_invalid"),
    ("float", "wav_unreadable"), ("extra_data", "wav_unreadable"),
])
def test_bad_wav_never_passes_for_existence(tmp_path, case, reason):
    path = write_wav(tmp_path / "out.wav", samples=0 if case == "empty" else 200,
                     channels=1 if case == "channels" else 2, rate=48000 if case == "rate" else 44100)
    data = bytearray(path.read_bytes())
    if case == "text":
        data = b"not audio"
    elif case == "truncated":
        data = data[:-4]  # Header is still readable by wave.
    elif case == "unaligned":
        data[40:44] = struct.pack("<I", len(data) - 45)
    elif case == "float":
        data[20:22] = struct.pack("<H", 3)
    elif case == "extra_data":
        data += b"undeclared trailing bytes"
    path.write_bytes(data)
    result = validate_wav(path, expected_channels=2)
    assert not result.artifacts_ok
    assert any(issue.startswith(reason) for issue in result.invalid)


def test_header_can_open_but_payload_is_still_rejected(tmp_path):
    path = write_wav(tmp_path / "out.wav")
    path.write_bytes(path.read_bytes()[:-20])
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 2
        assert audio.getnframes() == 200
    assert not validate_wav(path, expected_channels=2).artifacts_ok


def test_missing_and_inaccessible_wav_are_distinguished(tmp_path, monkeypatch):
    path = tmp_path / "out.wav"
    assert validate_wav(path, expected_channels=2).validity == "incomplete"
    write_wav(path)
    original_open = type(path).open

    def denied(current, *args, **kwargs):
        if current == path:
            raise PermissionError("read denied")
        return original_open(current, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", denied)
    assert validate_wav(path, expected_channels=2).validity == "not_verifiable"


@pytest.mark.parametrize("samples,target", [(200, 200/44100), (3, 2.5/44100), (1, 0.1/44100)])
def test_target_duration_matches_matlab_round_and_padding_contract(tmp_path, samples, target):
    result = validate_wav(write_wav(tmp_path / "out.wav", samples=samples), expected_channels=2)
    validate_dimensions(result, {"sample_rate_hz": 44100, "target_duration_s": target},
                        {"output_num_samples": samples, "output_num_channels": 2})
    assert result.artifacts_ok


def test_half_sample_does_not_use_python_bankers_rounding(tmp_path):
    result = validate_wav(write_wav(tmp_path / "out.wav", samples=2), expected_channels=2)
    validate_dimensions(result, {"sample_rate_hz": 44100, "target_duration_s": 2.5/44100}, None)
    assert "wav_target_duration_mismatch" in result.invalid


@pytest.mark.parametrize("target", [None, []])
def test_no_target_uses_observed_and_metadata_dimensions(tmp_path, target):
    result = validate_wav(write_wav(tmp_path / "out.wav", samples=123), expected_channels=2)
    validate_dimensions(result, {"target_duration_s": target}, {"output_num_samples": 123, "output_num_channels": 2})
    assert result.artifacts_ok


@pytest.mark.parametrize("field,value,reason", [
    ("output_num_samples", 1, "mismatch"), ("output_num_channels", 8, "mismatch"),
    ("output_num_samples", None, "invalid"), ("output_num_channels", True, "invalid"),
    ("output_num_samples", 0, "invalid"),
])
def test_metadata_dimensions_must_match_audio(tmp_path, field, value, reason):
    result = validate_wav(write_wav(tmp_path / "out.wav"), expected_channels=2)
    summary = {"output_num_samples": 200, "output_num_channels": 2, field: value}
    validate_dimensions(result, {}, summary)
    assert f"render_metadata_{field}_{reason}" in result.invalid


@pytest.mark.parametrize("target", [True, "10", 0, -1, float("nan"), float("inf")])
def test_invalid_target_duration_cannot_be_skipped(tmp_path, target):
    result = validate_wav(write_wav(tmp_path / "out.wav"), expected_channels=2)
    validate_dimensions(result, {"target_duration_s": target}, None)
    assert "runtime_target_duration_invalid" in result.invalid


def metadata_fixture(tmp_path, schema="3.0"):
    """Compact shape produced by export_render_metadata, including scalar structs."""
    manifest = {
        "schema_version": schema, "scene_id": "scene", "job_id": "job", "scene_type": "static",
        "project_name": "project", "render": {"sample_rate_hz": 44100, "active_output_type": "binaural_hrtf",
            "output_wav_path": (tmp_path / "out.wav").as_posix(),
            "output_metadata_path": (tmp_path / "out.json").as_posix()},
    }
    payload = {
        "schema_version": schema, "scene_id": "scene", "job_id": "job", "scene_type": "static",
        "project_name": "project", "sample_rate_hz": 44100, "output_type": "binaural_hrtf",
        "hrtf_id": "binaural_hrtf", "hrtf_path": (tmp_path / "receiver.daff").as_posix(),
        "output_wav_path": manifest["render"]["output_wav_path"],
        "output_metadata_path": manifest["render"]["output_metadata_path"],
        "summary": {"render_backend": "RAVEN + ITA", "output_num_samples": 200,
                    "output_num_channels": 2, "n_sources": 1, "sources": {"source_id": "src1"}},
    }
    path = tmp_path / "out.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, payload, path


@pytest.mark.parametrize("schema", ["1.0", "2.0", "3.0"])
def test_compact_exported_metadata_schemas_and_single_source(tmp_path, schema):
    manifest, payload, path = metadata_fixture(tmp_path, schema)
    result = validate_wav(write_wav(tmp_path / "out.wav"), expected_channels=2)
    read_render_metadata(result, path, manifest, required=True)
    assert result.artifacts_ok
    assert result.metadata == payload


@pytest.mark.parametrize("field", ["scene_id", "job_id", "scene_type", "project_name", "output_type",
                                  "output_wav_path", "output_metadata_path", "sample_rate_hz", "summary"])
def test_missing_metadata_fields_are_rejected(tmp_path, field):
    manifest, payload, path = metadata_fixture(tmp_path)
    payload.pop(field)
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = RenderValidation()
    read_render_metadata(result, path, manifest, required=True)
    assert not result.artifacts_ok
    assert any(field in issue for issue in result.invalid)


@pytest.mark.parametrize("content", ["{}", "[]", '{"x":NaN}', '{"x":1e999}', '{"scene_id":"a","scene_id":"b"}', '{"truncated":'])
def test_parseable_or_ambiguous_json_does_not_imply_valid_metadata(tmp_path, content):
    manifest, _, path = metadata_fixture(tmp_path)
    path.write_text(content, encoding="utf-8")
    result = RenderValidation()
    read_render_metadata(result, path, manifest, required=True)
    assert not result.artifacts_ok


@pytest.mark.parametrize("field,value", [("schema_version", "4.0"), ("schema_version", 3),
                                         ("sample_rate_hz", True), ("sample_rate_hz", 48000)])
def test_metadata_versions_and_numeric_types_are_checked(tmp_path, field, value):
    manifest, payload, path = metadata_fixture(tmp_path)
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = RenderValidation()
    read_render_metadata(result, path, manifest, required=True)
    assert not result.artifacts_ok


@pytest.mark.parametrize("field,value", [("scene_id", "other"), ("output_type", "other"), ("output_wav_path", "wrong.wav")])
def test_redundant_fields_cannot_hide_conflicting_identity(tmp_path, field, value):
    manifest, payload, path = metadata_fixture(tmp_path)
    payload["summary"][field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = RenderValidation()
    read_render_metadata(result, path, manifest, required=True)
    assert f"render_metadata_{field}_contradictory" in result.invalid


def test_metadata_required_missing_differs_from_optional_missing(tmp_path):
    manifest, _, path = metadata_fixture(tmp_path)
    path.unlink()
    required, optional = RenderValidation(), RenderValidation()
    read_render_metadata(required, path, manifest, required=True)
    read_render_metadata(optional, path, manifest, required=False)
    assert required.validity == "incomplete"
    assert optional.artifacts_ok


def source_fixture(tmp_path, schema="3.0"):
    manifest, payload, _ = metadata_fixture(tmp_path, schema)
    manifest["sources"] = [{
        "source_id": "src1", "event_type": "speech", "identity_id": "ID01", "audio_relpath": "ID01/source.wav",
        "audio_path": (tmp_path / "prepared.wav").as_posix(), "position_m": [1, 2, 3],
        "orientation_deg": {"yaw": 90, "pitch": 0, "roll": 10}, "gain_db": -3, "start_time_s": 0.5,
    }]
    reflected = schema in ("2.0", "3.0")
    payload["summary"]["sources"] = {
        "source_id": "src1", "source_name": "src1", "event_type": "speech", "identity_id": "ID01",
        "audio_relpath": "ID01/source.wav", "audio_path": manifest["sources"][0]["audio_path"],
        "directivity_path": "", "position_m": [1, 2, -3 if reflected else 3],
        "view_vector": [0, 0, -1 if reflected else 1], "up_vector": [0, 1, 0],
        "gain_db": -3, "source_start_time_s": 0.5, "source_end_time_s": 1.5, "source_original_duration_s": 1,
    }
    return manifest, payload


@pytest.mark.parametrize("schema", ["1.0", "2.0", "3.0"])
def test_source_pose_uses_exported_coordinate_frame(tmp_path, schema):
    manifest, payload = source_fixture(tmp_path, schema)
    result = RenderValidation(metadata=payload)
    validate_sources(result, manifest)
    assert result.artifacts_ok


@pytest.mark.parametrize("field", ["identity_id", "audio_relpath"])
def test_legacy_source_provenance_uses_empty_strings_not_inference(tmp_path, field):
    manifest, payload = source_fixture(tmp_path, "1.0")
    manifest["sources"][0].pop(field)
    payload["summary"]["sources"][field] = ""
    result = RenderValidation(metadata=payload)
    validate_sources(result, manifest)
    assert result.artifacts_ok


@pytest.mark.parametrize("field,value", [
    ("identity_id", "ID02"), ("audio_relpath", "ID02/source.wav"), ("audio_path", "wrong.wav"),
    ("event_type", "wrong"), ("directivity_path", "wrong.daff"),
    ("gain_db", -4), ("source_start_time_s", 0), ("source_end_time_s", 20),
    ("position_m", [1, 2, 3]), ("view_vector", [0, 0, 1]), ("up_vector", [1, 0, 0]),
    ("source_name", "wrong"), ("source_original_duration_s", True),
])
def test_source_correspondence_rejects_altered_metadata(tmp_path, field, value):
    manifest, payload = source_fixture(tmp_path)
    payload["summary"]["sources"][field] = value
    result = RenderValidation(metadata=payload)
    validate_sources(result, manifest)
    assert not result.artifacts_ok


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown"])
def test_sources_must_be_exact_set_without_duplicates(tmp_path, case):
    manifest, payload = source_fixture(tmp_path)
    item = payload["summary"]["sources"]
    payload["summary"]["sources"] = [] if case == "missing" else [item, dict(item)]
    if case == "unknown":
        payload["summary"]["sources"][1]["source_id"] = "unknown"
    result = RenderValidation(metadata=payload)
    validate_sources(result, manifest)
    assert "render_metadata_source_inventory_mismatch" in result.invalid


def provenance_fixture(tmp_path):
    manifest, payload = source_fixture(tmp_path)
    manifest["receiver"] = {"hrtfs": [{"hrtf_id": "binaural_hrtf", "hrtf_path": payload["hrtf_path"]}]}
    manifest["render"]["seed"] = 42
    payload.update(effective_seed=42, seed_source="render.seed")
    payload["summary"]["background_noise"] = {"enabled": False, "applied": False, "skipped_reason": "disabled", "layers": []}
    return manifest, payload


def test_receiver_ir_seed_and_disabled_noise_match(tmp_path):
    manifest, payload = provenance_fixture(tmp_path)
    result = RenderValidation(metadata=payload)
    validate_receiver_and_seed(result, manifest)
    validate_noise(result, manifest)
    assert result.artifacts_ok


@pytest.mark.parametrize("field,value", [("hrtf_id", "wrong"), ("hrtf_path", "wrong.daff"),
                                         ("effective_seed", 43), ("seed_source", "seed")])
def test_receiver_and_seed_provenance_rejects_substitution(tmp_path, field, value):
    manifest, payload = provenance_fixture(tmp_path)
    payload[field] = value
    result = RenderValidation(metadata=payload)
    validate_receiver_and_seed(result, manifest)
    assert not result.artifacts_ok


@pytest.mark.parametrize("seed_present", [True, False])
def test_legacy_and_unspecified_seed_normalization(tmp_path, seed_present):
    manifest, payload = provenance_fixture(tmp_path)
    manifest["render"].pop("seed")
    if seed_present:
        manifest["seed"] = 7
    payload.update(effective_seed=7 if seed_present else [], seed_source="seed" if seed_present else "unspecified")
    result = RenderValidation(metadata=payload)
    validate_receiver_and_seed(result, manifest)
    assert result.artifacts_ok


def array_fixture(tmp_path):
    manifest, payload = provenance_fixture(tmp_path)
    manifest["render"]["active_output_type"] = "circular_array"
    array = {"output_type": "circular_array", "num_microphones": 2, "radius_m": 0.05,
             "center_m": [2, 1.5, 2], "yaw_deg": 0, "microphones": [
                 {"microphone_index": 0, "channel_index": 1, "position_m": [2.05, 1.5, 2]},
                 {"microphone_index": 1, "channel_index": 2, "position_m": [1.95, 1.5, 2]},
             ]}
    manifest["receiver"] = {"hrtfs": [], "circular_array": array}
    payload.pop("hrtf_id")
    payload.pop("hrtf_path")
    payload.update(output_type="circular_array", receiver={"coordinate_frame": "public_xyz_y_up",
                                                         "circular_array": json.loads(json.dumps(array))})
    payload["summary"]["sources"].update(original_num_channels=2, downmix_policy="arithmetic_mean")
    return manifest, payload


@pytest.mark.parametrize("case", ["valid", "geometry", "order", "frame", "fake_ir", "downmix"])
def test_array_provenance_includes_order_and_downmix(tmp_path, case):
    manifest, payload = array_fixture(tmp_path)
    if case == "geometry":
        payload["receiver"]["circular_array"]["radius_m"] = 0.06
    elif case == "order":
        payload["receiver"]["circular_array"]["microphones"].reverse()
    elif case == "frame":
        payload["receiver"]["coordinate_frame"] = "raven"
    elif case == "fake_ir":
        payload["hrtf_id"] = "fake"
    elif case == "downmix":
        payload["summary"]["sources"]["downmix_policy"] = "none"
    result = RenderValidation(metadata=payload)
    validate_receiver_and_seed(result, manifest)
    assert result.artifacts_ok is (case == "valid")


def noise_fixture(tmp_path, *, skipped=False):
    manifest, payload = provenance_fixture(tmp_path)
    manifest["background_noise"] = {"enabled": True, "layers": [{"strategy": "colored", "color": "white", "snr_db": 10}]}
    layer = {"strategy_original": "colored", "strategy": "colored", "color": "white", "snr_db": 10,
             "seed": [], "effective_seed": 42, "source_num_channels": 1, "target_num_channels": 2,
             "source_num_samples": 200, "target_num_samples": 200,
             "duration_mode": "exact", "channel_mode": "mono_replicated", "skipped": skipped,
             "applied": not skipped, "skipped_reason": "silent_signal" if skipped else ""}
    payload["summary"]["background_noise"] = {"enabled": True, "applied": not skipped,
                                              "skipped_reason": "all_layers_skipped" if skipped else "",
                                              "layers": layer}
    return manifest, payload


@pytest.mark.parametrize("skipped", [True, False])
def test_noise_applied_or_supported_skip_is_coherent(tmp_path, skipped):
    manifest, payload = noise_fixture(tmp_path, skipped=skipped)
    result = validate_wav(write_wav(tmp_path / "out.wav"), expected_channels=2)
    result.metadata = payload
    validate_noise(result, manifest)
    assert result.artifacts_ok


@pytest.mark.parametrize("field,value", [("strategy", "wrong"), ("snr_db", 9), ("color", "pink"),
    ("effective_seed", 0), ("target_num_samples", 1), ("channel_mode", "channel_to_channel"),
    ("duration_mode", "loop"), ("applied", False), ("source_num_channels", 8)])
def test_noise_metadata_matches_configuration_and_shape(tmp_path, field, value):
    manifest, payload = noise_fixture(tmp_path)
    payload["summary"]["background_noise"]["layers"][field] = value
    result = validate_wav(write_wav(tmp_path / "out.wav"), expected_channels=2)
    result.metadata = payload
    validate_noise(result, manifest)
    assert not result.artifacts_ok


def test_audio_folder_noise_normalizes_to_selected_audio_file(tmp_path):
    manifest, payload = noise_fixture(tmp_path)
    path = (tmp_path / "selected.wav").as_posix()
    manifest["background_noise"]["layers"][0].update(strategy="audio_folder", path=path)
    layer = payload["summary"]["background_noise"]["layers"]
    layer.update(strategy_original="audio_folder", strategy="audio_file", path=path)
    result = RenderValidation(metadata=payload)
    validate_noise(result, manifest)
    assert result.artifacts_ok


def test_disabled_noise_cannot_claim_to_have_been_applied(tmp_path):
    manifest, payload = provenance_fixture(tmp_path)
    payload["summary"]["background_noise"]["applied"] = True
    result = RenderValidation(metadata=payload)
    validate_noise(result, manifest)
    assert "background_noise_disabled_inconsistent" in result.invalid


def t30_fixture(values=None):
    values = [0.5] * 10 if values is None else values
    valid = [type(value) in (float, int) and value > 0 for value in values]
    return {"summary": {"room": {"reverberation": {
        "metric": "T30", "estimated_from": "room", "method": "rpf.getT30", "t30_s": values,
        "band_frequencies_hz": list(RAVEN_OCTAVE_FREQUENCIES_HZ) if len(values) == 10 else [],
        "band_resolution": "octave", "frequency_mapping": "center_frequency", "valid_band_count": sum(valid),
        "mean_t30_s": sum(values)/10 if all(valid) and len(values) == 10 else [],
        "status": "estimated" if all(valid) and len(values) == 10 else "unavailable",
    }}}}


def test_valid_t30_checks_bands_and_derived_summary_without_sabine_bin():
    result = RenderValidation(metadata=t30_fixture([10.0] * 10))
    validate_t30(result)
    assert result.validity == "valid"  # No new Sabine-range requirement.


@pytest.mark.parametrize("value", [0, -1, None, True, "0.5"])
def test_bad_t30_band_is_acoustic_rejection_when_summary_is_coherent(value):
    values = [0.5] * 9 + [value]
    result = RenderValidation(metadata=t30_fixture(values))
    validate_t30(result)
    assert result.artifacts_ok
    assert result.acoustic_rejection["reason"] == "raven_t30_non_finite_or_non_positive"
    assert result.validity == "invalid"


@pytest.mark.parametrize("case", ["missing", "wrong_count"])
def test_t30_missing_or_wrong_band_count_retains_rejection_policy(case):
    result = RenderValidation(metadata={"summary": {}} if case == "missing" else t30_fixture([0.5] * 9))
    validate_t30(result)
    assert result.artifacts_ok
    assert result.acoustic_rejection["reason"] == ("raven_t30_missing" if case == "missing" else "raven_t30_band_count")


@pytest.mark.parametrize("field,value", [("mean_t30_s", 2), ("valid_band_count", 9), ("status", "unavailable"),
    ("band_frequencies_hz", [100] * 10), ("method", "unknown"), ("valid_band_count", True)])
def test_inconsistent_t30_derived_fields_are_metadata_errors(field, value):
    payload = t30_fixture()
    payload["summary"]["room"]["reverberation"][field] = value
    result = RenderValidation(metadata=payload)
    validate_t30(result)
    assert not result.artifacts_ok
    assert result.acoustic_rejection is None


def test_corrupt_json_does_not_become_a_t30_retry(tmp_path):
    manifest, _, path = metadata_fixture(tmp_path)
    path.write_text('{"summary":', encoding="utf-8")
    result = RenderValidation()
    read_render_metadata(result, path, manifest, required=True)
    validate_t30(result)
    assert not result.artifacts_ok
    assert result.acoustic_rejection is None


def test_large_matlab_sample_count_in_scientific_notation_matches_full_wav(tmp_path):
    manifest, payload, path = metadata_fixture(tmp_path)
    payload["summary"]["output_num_samples"] = 1000000
    text = json.dumps(payload).replace('"output_num_samples": 1000000', '"output_num_samples": 1.0E+6')
    path.write_text(text, encoding="utf-8")
    result = validate_wav(write_wav(tmp_path / "out.wav", samples=1000000), expected_channels=2)
    read_render_metadata(result, path, manifest, required=True)
    assert result.artifacts_ok
    assert type(result.metadata["summary"]["output_num_samples"]) is int
