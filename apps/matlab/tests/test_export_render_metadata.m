function tests = test_export_render_metadata
    tests = functiontests(localfunctions);
end

function testWritesCompactMetadataWithoutLegacyOutputCounters(testCase)
    cfg = struct( ...
        'schema_version', '1.0', ...
        'scene_id', 'scene_static_0001', ...
        'job_id', 'job_001', ...
        'scene_type', 'static', ...
        'render', struct('effective_seed', 123, 'seed_source', 'render.seed'));

    result = struct();
    result.fs = 44100;
    result.audio = ones(32, 2);
    result.hrtf_id = 'subject-a';
    result.hrtf_path = 'a.daff';
    result.output = struct('wav_path', 'out.wav', 'metadata_path', 'meta.json', 'project_name', 'project');
    result.summary = struct( ...
        'scene_type', 'static', ...
        'render_backend', 'RAVEN + ITA', ...
        'n_sources', 1, ...
        'room', struct('reverberation', struct( ...
            'metric', 'T30', ...
            'estimated_from', 'room', ...
            'method', 'rpf.getT30', ...
            't30_s', [0.7 0.8 0.9 1.0 1.1 1.2 1.3 1.4 1.5 1.6], ...
            'band_frequencies_hz', [31.5 63 125 250 500 1000 2000 4000 8000 16000], ...
            'band_resolution', 'octave', ...
            'frequency_mapping', 'center_frequency', ...
            'valid_band_count', 10, ...
            'mean_t30_s', 1.15, ...
            'status', 'estimated')), ...
        'sources', struct('source_id', 'src1', 'source_start_time_s', 0.0, 'source_end_time_s', 1.0), ...
        'output_num_samples', 32, ...
        'output_num_channels', 2);

    batch = struct('n_variants', 1, 'variants', struct( ...
        'hrtf_id', 'subject-a', 'output_wav_path', 'out.wav', ...
        'output_metadata_path', 'meta.json'));
    output_path = [tempname '.json'];
    cleanup = onCleanup(@() delete_if_exists(output_path)); %#ok<NASGU>

    export_render_metadata(cfg, result, output_path, batch);
    meta = jsondecode(fileread(output_path));

    verifyEqual(testCase, meta.scene_id, 'scene_static_0001');
    verifyEqual(testCase, meta.scene_type, 'static');
    verifyFalse(testCase, isfield(meta.summary, 'scene_type'));
    verifyEqual(testCase, meta.summary.render_backend, 'RAVEN + ITA');
    verifyEqual(testCase, meta.summary.sources.source_end_time_s, 1.0);
    verifyEqual(testCase, meta.summary.room.reverberation.mean_t30_s, 1.15, 'AbsTol', 1e-12);
    verifyEqual(testCase, transpose(meta.summary.room.reverberation.t30_s), ...
        [0.7 0.8 0.9 1.0 1.1 1.2 1.3 1.4 1.5 1.6], 'AbsTol', 1e-12);
    verifyEqual(testCase, transpose(meta.summary.room.reverberation.band_frequencies_hz), ...
        [31.5 63 125 250 500 1000 2000 4000 8000 16000]);
    verifyEqual(testCase, meta.summary.room.reverberation.band_resolution, 'octave');
    verifyEqual(testCase, meta.summary.room.reverberation.frequency_mapping, 'center_frequency');
    verifyEqual(testCase, meta.summary.room.reverberation.valid_band_count, 10);
    verifyTrue(testCase, isfield(meta, 'batch'));
    verifyEqual(testCase, meta.batch.n_variants, 1);
    verifyFalse(testCase, isfield(meta.batch, 'other_variants'));
    verifyFalse(testCase, isfield(meta.batch, 'scene_type'));
    verifyFalse(testCase, isfield(meta, 'output_num_samples'));
    verifyFalse(testCase, isfield(meta, 'output_num_channels'));
end

function testBatchOnlyListsOtherVariants(testCase)
    cfg = struct('schema_version', '1.0', 'scene_id', 'scene_static_0001', ...
        'job_id', 'job_001', 'scene_type', 'static', ...
        'render', struct('effective_seed', 123, 'seed_source', 'render.seed'));
    result = struct('fs', 44100, 'hrtf_id', 'subject-a', 'hrtf_path', 'a.daff', ...
        'output', struct('wav_path', 'a.wav', 'metadata_path', 'a.json', 'project_name', 'project-a'), ...
        'summary', struct('scene_type', 'static', 'n_sources', 2));
    variants(1) = struct('hrtf_id', 'subject-a', 'output_wav_path', 'a.wav', 'output_metadata_path', 'a.json');
    variants(2) = struct('hrtf_id', 'subject-b', 'output_wav_path', 'b.wav', 'output_metadata_path', 'b.json');
    batch = struct('n_variants', 2, 'variants', variants);
    output_path = [tempname '.json'];
    cleanup = onCleanup(@() delete_if_exists(output_path)); %#ok<NASGU>

    export_render_metadata(cfg, result, output_path, batch);
    meta = jsondecode(fileread(output_path));

    verifyEqual(testCase, meta.hrtf_id, 'subject-a');
    verifyEqual(testCase, meta.output_wav_path, 'a.wav');
    verifyEqual(testCase, meta.batch.n_variants, 2);
    verifyEqual(testCase, meta.batch.other_variants.hrtf_id, 'subject-b');
    verifyEqual(testCase, meta.batch.other_variants.output_wav_path, 'b.wav');
    verifyEqual(testCase, meta.batch.other_variants.output_metadata_path, 'b.json');
    verifyFalse(testCase, isfield(meta.batch, 'scene_id'));
    verifyFalse(testCase, isfield(meta.batch, 'effective_seed'));
end

function testArrayExportHasPublicProvenanceWithoutFakeHRTF(testCase)
    cfg = struct('schema_version', '3.0', 'scene_id', 'scene', 'job_id', 'job', 'scene_type', 'static');
    microphones = repmat(struct('microphone_index', 0, 'channel_index', 0, 'position_m', []), 1, 8);
    for k = 1:8
        microphones(k) = struct('microphone_index', k-1, 'channel_index', k, ...
            'position_m', [2+0.05*cosd(45*(k-1)),1.5,2+0.05*sind(45*(k-1))]);
    end
    array = struct('num_microphones', 8, 'radius_m', 0.05, 'center_m', [2 1.5 2], ...
        'yaw_deg', 0, 'microphones', microphones);
    wav_path = [tempname '.wav'];
    metadata_path = [tempname '.json'];
    testCase.addTeardown(@() delete_if_exists(wav_path));
    testCase.addTeardown(@() delete_if_exists(metadata_path));
    result = struct('fs', 44100, 'audio', 0.1*ones(32,8), 'hrtf_id', '', 'hrtf_path', '', ...
        'output', struct('output_type', 'circular_array', 'wav_path', wav_path, ...
            'metadata_path', metadata_path, 'project_name', 'array_project'), ...
        'summary', struct('output_num_channels', 8, 'receiver', struct('circular_array', array), ...
            'sources', struct('original_num_channels', 2, 'downmix_policy', 'arithmetic_mean'), ...
            'room', struct('reverberation', struct('t30_s', ones(1,10)))));
    write_output_wav(result.audio, result.fs, wav_path);
    export_render_metadata(cfg, result, metadata_path);
    meta = jsondecode(fileread(metadata_path));
    info = audioinfo(wav_path);
    verifyEqual(testCase, info.NumChannels, 8);
    verifyEqual(testCase, info.SampleRate, 44100);
    verifyEqual(testCase, meta.output_type, 'circular_array');
    verifyFalse(testCase, isfield(meta, 'hrtf_id'));
    verifyFalse(testCase, isfield(meta, 'hrtf_path'));
    verifyEqual(testCase, meta.receiver.coordinate_frame, 'public_xyz_y_up');
    verifyEqual(testCase, [meta.receiver.circular_array.microphones.channel_index], 1:8);
    verifyEqual(testCase, meta.summary.sources.downmix_policy, 'arithmetic_mean');
    verifyEqual(testCase, numel(meta.summary.room.reverberation.t30_s), 10);
end

function testSourceProvenanceSurvivesContextSummaryAndJsonForAllOutputs(testCase)
    cfg = struct('schema_version', '3.0', 'scene_id', 'scene', ...
        'job_id', 'job', 'scene_type', 'static');
    base = struct('source_id', 'src_grouped', 'audio_path', 'C:/prepared/src__trim_1.wav', ...
        'event_type', 'speech', 'position_m', [1 2 3], ...
        'orientation_deg', struct('yaw', 0, 'pitch', 0, 'roll', 0), ...
        'identity_id', 'ID02', 'audio_relpath', 'ID02/long.wav');
    flat = base;
    flat.source_id = 'src_flat';
    flat.identity_id = '';
    flat.audio_relpath = 'clip_ID13.wav';
    legacy = base;
    legacy.source_id = 'src_legacy';
    legacy.identity_id = [];
    legacy.audio_relpath = [];
    cfg.sources = [base flat legacy];
    ctx = build_sources_from_config(struct('manifest', cfg, 'rpf', MockRavenProject()));
    summaries = preallocate_static_source_summaries(3);
    for i = 1:3
        summaries(i) = build_static_source_summary(ctx.sources(i), ctx.sourceNames{i}, ...
            ctx.sourcePositions(i,:), ctx.sourceViewVectors(i,:), ctx.sourceUpVectors(i,:), 0, 1);
    end
    outputs = {'binaural_hrtf', 'bte_rear_hartf', 'bte_front_hartf', 'circular_array'};
    for i = 1:numel(outputs)
        output_path = [tempname '.json'];
        testCase.addTeardown(@() delete_if_exists(output_path));
        result = struct('fs', 44100, 'summary', struct('sources', summaries), ...
            'output', struct('output_type', outputs{i}, 'wav_path', 'render.wav', ...
                'metadata_path', output_path, 'project_name', 'project'));
        export_render_metadata(cfg, result, output_path);
        meta = jsondecode(fileread(output_path));
        verifyEqual(testCase, meta.output_type, outputs{i});
        verifyEqual(testCase, meta.output_wav_path, 'render.wav');
        verifyEqual(testCase, meta.project_name, 'project');
        verifyEqual(testCase, meta.output_metadata_path, output_path);
        verifyEqual(testCase, {meta.summary.sources.source_id}, {'src_grouped', 'src_flat', 'src_legacy'});
        verifyEqual(testCase, {meta.summary.sources.identity_id}, {'ID02', '', ''});
        verifyEqual(testCase, {meta.summary.sources.audio_relpath}, {'ID02/long.wav', 'clip_ID13.wav', ''});
        verifyEqual(testCase, {meta.summary.sources.audio_path}, repmat({base.audio_path}, 1, 3));
    end
end

function delete_if_exists(path_value)
    if isfile(path_value)
        delete(path_value);
    end
end
