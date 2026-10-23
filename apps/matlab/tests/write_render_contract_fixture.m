function write_render_contract_fixture(output_dir, schema_version)
    % Fixture de interoperabilidad emitido por escritores reales, sin simulación.
    % No representa una validación física de RAVEN.
    if nargin < 2, schema_version = '3.0'; end
    if ~isfolder(output_dir), mkdir(output_dir); end
    output_dir = char(java.io.File(output_dir).getCanonicalPath());
    fs = 44100;
    source_path = fullfile(output_dir, 'source.wav');
    write_output_wav(0.1*ones(200, 1), fs, source_path);
    source = struct('source_id', 'src1', 'event_type', 'speech', ...
        'identity_id', 'ID01', 'audio_relpath', 'ID01/source.wav', ...
        'audio_path', source_path, 'position_m', [1 1 1], ...
        'orientation_deg', struct('yaw', 90, 'pitch', 0, 'roll', 0), ...
        'gain_db', 0, 'start_time_s', 0);
    output = struct('project_name', 'project', 'output_type', 'binaural_hrtf', ...
        'wav_path', fullfile(output_dir, 'out.wav'), 'metadata_path', fullfile(output_dir, 'out.json'));
    hrtf_path = fullfile(output_dir, 'receiver.daff');
    cfg = struct('schema_version', schema_version, 'scene_type', 'static', ...
        'scene_id', 'scene', 'job_id', 'job', 'project_name', 'project', ...
        'sources', source, 'receiver', struct('hrtfs', struct('hrtf_id', 'binaural_hrtf', 'hrtf_path', hrtf_path)), ...
        'render', struct('sample_rate_hz', fs, 'active_output_type', 'binaural_hrtf', ...
            'output_wav_path', output.wav_path, 'output_metadata_path', output.metadata_path, ...
            'outputs', output, 'seed', 42, 'effective_seed', 42, 'seed_source', 'render.seed'));
    manifest_path = fullfile(output_dir, 'input_manifest.json');
    write_json(manifest_path, cfg);
    if strcmp(schema_version, '1.0')
        position = source.position_m;
        [view, up] = orientation_deg_to_vectors(0, 90);
    else
        [position, view, up] = transform_schema2_pose_to_raven(source.position_m, source.orientation_deg);
    end
    summary = struct('render_backend', 'RAVEN + ITA', 'n_sources', 1, ...
        'sources', build_static_source_summary(source, source.source_id, position, view, up, 0, 200/fs), ...
        'output_num_samples', 200, 'output_num_channels', 2, ...
        'room', struct('reverberation', build_room_reverberation_summary(ones(1,10))));
    audio = 0.1*ones(200, 2);
    [audio, summary.background_noise] = apply_background_noise(audio, fs, struct('enabled', false));
    result = struct('fs', fs, 'audio', audio, 'summary', summary, 'output', output, ...
        'hrtf_id', 'binaural_hrtf', 'hrtf_path', hrtf_path);
    write_output_wav(audio, fs, output.wav_path);
    export_render_metadata(cfg, result, output.metadata_path);
    context = struct('attempt_id', repmat('a', 1, 32), 'variant_id', 'scene__binaural_hrtf', ...
        'manifest_sha256', sha256_file(manifest_path), ...
        'completion_path', fullfile(output_dir, 'completion.json'));
    write_render_completion(manifest_path, cfg, context);
    expected = context;
    expected.scene_id = cfg.scene_id;
    expected.output_type = 'binaural_hrtf';
    expected.manifest_snapshot_path = manifest_path;
    expected.expected_artifacts = [struct('kind', 'wav', 'path', output.wav_path), ...
        struct('kind', 'metadata', 'path', output.metadata_path)];
    write_json(fullfile(output_dir, 'expectation.json'), expected);
end

function write_json(path_value, payload)
    bytes = unicode2native(jsonencode(payload, 'PrettyPrint', true), 'UTF-8');
    fid = fopen(path_value, 'wb');
    assert(fid ~= -1);
    assert(fwrite(fid, bytes, 'uint8') == numel(bytes));
    assert(fclose(fid) == 0);
end
