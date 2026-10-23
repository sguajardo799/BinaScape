function result = render_static_scene(cfg, diagnostics)
    % Renderiza una escena estática.
    %
    % Requiere:
    %   - prepare_raven_project(cfg)
    %   - build_room_from_config(ctx)
    %   - build_receiver_from_pose(ctx)
    %   - build_sources_from_config(ctx)
    %
    % Cada fuente en cfg.sources debería incluir idealmente:
    %   source_id
    %   audio_path
    %   position_m
    %   orientation_deg
    % Opcionales:
    %   gain_db
    %   start_time_s
    %
    % Salida:
    %   result.audio   -> matriz [muestras x canales] double
    %   result.fs      -> frecuencia de muestreo
    %   result.summary -> struct con metadata resumida

    if nargin < 2
        diagnostics = [];
    end
    if ~isstruct(diagnostics) || ~isfield(diagnostics, 'initialization_started') || ~diagnostics.initialization_started
        emit_render_event(diagnostics, 'stage_started', 'initialization');
    end
    fs = 44100;
    if double(cfg.render.sample_rate_hz) ~= fs
        error('BinaScape:Matlab:UnsupportedSampleRate', ...
            'El backend MATLAB/RAVEN requiere render.sample_rate_hz=%d Hz; recibido %d Hz.', ...
            fs, double(cfg.render.sample_rate_hz));
    end
    n_runs = numel(cfg.render.outputs);
    runs = preallocate_static_runs(n_runs);

    for i = 1:n_runs
        if isfield(cfg.render, 'receiver_variants') && ...
                strcmp(cfg.render.receiver_variants(i).receiver_kind, 'circular_array')
            active_hrtf = [];
        elseif isfield(cfg.render, 'receiver_variants')
            active_hrtf = cfg.receiver.hrtfs(cfg.render.receiver_variants(i).hrtf_index);
        else
            active_hrtf = cfg.receiver.hrtfs(i);
        end
        runs(i) = render_single_receiver_run(cfg, active_hrtf, cfg.render.outputs(i), fs, diagnostics, i > 1);
    end

    result = struct();
    result.fs = fs;
    result.runs = runs;
    result.batch = build_batch_summary(runs);
    result.summary = result.batch;

    if n_runs == 1
        result.audio = runs(1).audio;
        result.summary = runs(1).summary;
    else
        result.audio = [];
    end
end

function run_result = render_single_receiver_run(cfg, active_hrtf, output_cfg, fs, diagnostics, start_initialization)
    run_manifest = cfg;
    is_array = isempty(active_hrtf);
    if ~is_array
        run_manifest.receiver.active_hrtf = active_hrtf;
    end

    run_ctx = struct();
    if is_array
        run_ctx.output_type = 'circular_array';
        event_details = struct('output_type', 'circular_array');
    else
        run_ctx.hrtf_id = active_hrtf.hrtf_id;
        event_details = struct('output_type', active_hrtf.hrtf_id);
    end
    if start_initialization
        emit_render_event(diagnostics, 'stage_started', 'initialization', event_details);
    end
    run_ctx.project_name = output_cfg.project_name;
    run_ctx.effective_seed = cfg.render.effective_seed;
    run_ctx.seed_source = cfg.render.seed_source;

    ctx = prepare_raven_project(run_manifest, run_ctx);
    ctx = build_room_from_config(ctx);
    if is_array
        ctx = build_circular_array_receiver(ctx);
    else
        ctx = build_receiver_from_pose(ctx);
    end
    ctx = build_sources_from_config(ctx);

    rpf = ctx.rpf;
    rpf.setGenerateRIR(double(is_array));
    rpf.setGenerateBRIR(double(~is_array));
    rpf.setSimulationTypeRT(1);
    rpf.setSimulationTypeIS(1);
    rpf.setNumParticles(60000);
    rpf.setISOrder_PS(2);
    rpf.setFixReflectionPattern(1);
    rpf.setFixPoissonSequence(1);
    %rpf.plotModel;

    emit_render_event(diagnostics, 'stage_completed', 'initialization', event_details);
    emit_render_event(diagnostics, 'stage_started', 'simulation', event_details);
    step_details = event_details;
    step_details.step = 'raven_run';
    emit_render_event(diagnostics, 'step_started', 'simulation', step_details);
    rpf.run();
    emit_render_event(diagnostics, 'step_completed', 'simulation', step_details);
    step_details.step = 'audio_postprocessing';
    emit_render_event(diagnostics, 'step_started', 'simulation', step_details);
    if is_array
        channels = cfg.receiver.circular_array.num_microphones;
        BRIR = collect_circular_array_responses(rpf, ctx.n_sources, channels, fs);
        room_reverberation = build_room_reverberation_summary(rpf.getT30(true, false));
    else
        channels = 2;
        BRIR = rpf.getBinauralImpulseResponseItaAudio();
        room_reverberation = build_room_reverberation_summary(rpf.getT30());
    end

    n_sources = ctx.n_sources;
    if numel(BRIR) ~= n_sources
        error('Cantidad de BRIR (%d) no coincide con cantidad de fuentes (%d).', numel(BRIR), n_sources);
    end

    samples_tail = (rpf.filterLength / 1000) * fs;
    [mix, mix_summary] = mix_rendered_sources(ctx, BRIR, fs, room_reverberation, samples_tail, channels, is_array);
    if is_array
        mix_summary.receiver = struct('circular_array', cfg.receiver.circular_array);
    end
    [mix, background_noise_summary] = apply_background_noise(mix, fs, cfg.background_noise, run_ctx);
    mix_summary.background_noise = background_noise_summary;

    run_result = struct();
    run_result.hrtf_id = '';
    run_result.hrtf_path = '';
    if ~is_array
        run_result.hrtf_id = active_hrtf.hrtf_id;
        run_result.hrtf_path = active_hrtf.hrtf_path;
    end
    run_result.output = output_cfg;
    run_result.audio = mix;
    run_result.fs = fs;
    run_result.summary = mix_summary;
    emit_render_event(diagnostics, 'step_completed', 'simulation', step_details);
    emit_render_event(diagnostics, 'stage_completed', 'simulation', event_details);
end

function [mix, summary] = mix_rendered_sources(ctx, BRIR, fs, room_reverberation, filter_length, channels, downmix)
    n_sources = ctx.n_sources;
    rendered_sources = cell(n_sources, 1);
    source_entries = preallocate_static_source_summaries(n_sources);
    source_lengths = zeros(n_sources, 1);
    source_offsets = zeros(n_sources, 1);
    source_policies = cell(n_sources, 1);

    max_total_len = 0;

    for s = 1:n_sources
        src = ctx.sources(s);

        if ~isfield(src, 'audio_path') || isempty(src.audio_path)
            error('La fuente %d (%s) no tiene audio_path definido.', s, src.source_id);
        end

        if ~isfile(src.audio_path)
            error('Archivo de audio no encontrado para fuente %d: %s', s, src.audio_path);
        end

        audio_path = fullfile(dir(src.audio_path).folder, dir(src.audio_path).name);
        source_info = audioinfo(audio_path);
        source_ita = ita_read(audio_path);
        source_ita = resample_ita_audio_if_needed(source_ita, fs, s);

        original_duration_s = double(source_info.TotalSamples) / double(source_info.SampleRate);

        gain_db = 0.0;
        if isfield(src, 'gain_db') && ~isempty(src.gain_db)
            gain_db = double(src.gain_db);
        end
        [source_ita, source_policies{s}] = prepare_receiver_source_audio(source_ita, gain_db, downmix);

        source_binaural = ita_convolve(source_ita, BRIR(s));

        start_time_s = 0.0;
        if isfield(src, 'start_time_s') && ~isempty(src.start_time_s)
            start_time_s = double(src.start_time_s);
        end
        offset_samples = max(0, round(start_time_s * fs));

        binaural_td = source_binaural.timeData;
        if size(binaural_td, 2) ~= channels
            error('La convolución de la fuente %d no produjo los %d canales esperados.', s, channels);
        end

        if should_trim_reverb_tail(ctx.manifest)
            binaural_td = trim_rendered_source_reverb_tail(binaural_td, filter_length);
        end

        rendered_sources{s} = binaural_td;
        source_lengths(s) = size(binaural_td, 1);
        source_offsets(s) = offset_samples;
        source_entries(s) = build_static_source_summary( ...
            src, ...
            ctx.sourceNames{s}, ...
            ctx.sourcePositions(s, :), ...
            ctx.sourceViewVectors(s, :), ...
            ctx.sourceUpVectors(s, :), ...
            start_time_s, ...
            original_duration_s);
        max_total_len = max(max_total_len, offset_samples + source_lengths(s));
    end

    mix = zeros(max_total_len, channels);
    for s = 1:n_sources
        y = rendered_sources{s};
        if isempty(y)
            continue;
        end
        start_idx = source_offsets(s) + 1;
        end_idx = start_idx + size(y, 1) - 1;
        mix(start_idx:end_idx, :) = mix(start_idx:end_idx, :) + y;
    end

    mix = trim_audio_to_target_duration(mix, fs, get_target_duration_s(ctx.manifest));

    if isempty(mix)
        mix = zeros(0, channels);
    end

    peak = max(abs(mix), [], 'all');
    if ~isempty(peak) && peak > 0.999
        mix = 0.999 * mix / peak;
    end

    summary = struct();
    summary.scene_type = 'static';
    summary.render_backend = 'RAVEN + ITA';
    summary.n_sources = n_sources;
    summary.room = struct('reverberation', room_reverberation);
    summary.sources = source_entries;
    if downmix
        for s = 1:n_sources
            summary.sources(s).original_num_channels = source_policies{s}.original_num_channels;
            summary.sources(s).downmix_policy = source_policies{s}.downmix_policy;
        end
    end
    summary.output_num_samples = size(mix, 1);
    summary.output_num_channels = size(mix, 2);
end

function tf = should_trim_reverb_tail(manifest)
    tf = false;

    if isfield(manifest, 'render') && isfield(manifest.render, 'trim_reverb_tail')
        tf = logical(manifest.render.trim_reverb_tail);
    end
end

function target_duration_s = get_target_duration_s(manifest)
    target_duration_s = [];

    if isfield(manifest, 'render') && isfield(manifest.render, 'target_duration_s')
        target_duration_s = manifest.render.target_duration_s;
    end
end

function batch_summary = build_batch_summary(runs)
    batch_summary = struct();
    batch_summary.n_variants = numel(runs);
    variants = cell(1, numel(runs));
    for i = 1:numel(runs)
        variant = struct('output_type', get_render_output_type(runs(i)), ...
            'output_wav_path', runs(i).output.wav_path, ...
            'output_metadata_path', runs(i).output.metadata_path);
        if ~isempty(runs(i).hrtf_id)
            variant.hrtf_id = runs(i).hrtf_id;
        end
        variants{i} = variant;
    end
    batch_summary.variants = variants;
end
