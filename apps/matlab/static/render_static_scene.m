function result = render_static_scene(cfg)
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
    %   result.audio   -> matriz [N x 2] double
    %   result.fs      -> frecuencia de muestreo
    %   result.summary -> struct con metadata resumida

    fs = cfg.render.sample_rate_hz;
    n_runs = numel(cfg.receiver.hrtfs);
    runs = preallocate_static_runs(n_runs);

    for i = 1:n_runs
        runs(i) = render_single_hrtf_run(cfg, cfg.receiver.hrtfs(i), cfg.render.outputs(i), fs);
    end

    result = struct();
    result.fs = fs;
    result.runs = runs;
    result.summary = build_batch_summary(cfg, runs);

    if n_runs == 1
        result.audio = runs(1).audio;
        result.summary = runs(1).summary;
    else
        result.audio = [];
    end
end

function run_result = render_single_hrtf_run(cfg, active_hrtf, output_cfg, fs)
    run_manifest = cfg;
    run_manifest.receiver.active_hrtf = active_hrtf;

    run_ctx = struct();
    run_ctx.hrtf_id = active_hrtf.hrtf_id;
    run_ctx.project_name = output_cfg.project_name;
    run_ctx.effective_seed = cfg.render.effective_seed;
    run_ctx.seed_source = cfg.render.seed_source;

    ctx = prepare_raven_project(run_manifest, run_ctx);
    ctx = build_room_from_config(ctx);
    ctx = build_receiver_from_pose(ctx);
    ctx = build_sources_from_config(ctx);

    rpf = ctx.rpf;
    rpf.setGenerateRIR(0);
    rpf.setGenerateBRIR(1);
    rpf.setSimulationTypeRT(1);
    rpf.setSimulationTypeIS(1);
    rpf.setNumParticles(60000);
    rpf.setISOrder_PS(2);
    rpf.setFixReflectionPattern(1);
    rpf.setFixPoissonSequence(1);
    %rpf.plotModel;

    rpf.run();
    BRIR = rpf.getBinauralImpulseResponseItaAudio();
    room_reverberation = build_room_reverberation_summary(rpf.getT30());

    n_sources = ctx.n_sources;
    if numel(BRIR) ~= n_sources
        error('Cantidad de BRIR (%d) no coincide con cantidad de fuentes (%d).', numel(BRIR), n_sources);
    end

    samples_tail = (rpf.filterLength / 1000) * cfg.render.sample_rate_hz;
    [mix, mix_summary] = mix_rendered_sources(ctx, BRIR, fs, room_reverberation, samples_tail);
    [mix, background_noise_summary] = apply_background_noise(mix, fs, cfg.background_noise, run_ctx);
    mix_summary.background_noise = background_noise_summary;

    run_result = struct();
    run_result.hrtf_id = active_hrtf.hrtf_id;
    run_result.hrtf_path = active_hrtf.hrtf_path;
    run_result.output = output_cfg;
    run_result.audio = mix;
    run_result.fs = fs;
    run_result.summary = mix_summary;
end

function [mix, summary] = mix_rendered_sources(ctx, BRIR, fs, room_reverberation, filter_length)
    n_sources = ctx.n_sources;
    rendered_sources = cell(n_sources, 1);
    source_entries = preallocate_static_source_summaries(n_sources);
    source_lengths = zeros(n_sources, 1);
    source_offsets = zeros(n_sources, 1);

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

        if source_ita.samplingRate ~= fs
            error(['La fuente %d tiene fs=%d Hz, pero la escena espera fs=%d Hz. ', ...
                   'Re-muestrea antes o agrega una etapa de resampling.'], ...
                    s, source_ita.samplingRate, fs);
        end

        original_duration_s = double(source_info.TotalSamples) / double(source_info.SampleRate);
        %% source_ita.timeData = normalize_audio_peak(source_ita.timeData, 0.99);

        gain_db = 0.0;
        if isfield(src, 'gain_db') && ~isempty(src.gain_db)
            gain_db = double(src.gain_db);
        end
        gain_lin = 10^(gain_db / 20);
        source_ita.timeData = source_ita.timeData * gain_lin;

        source_binaural = ita_convolve(source_ita, BRIR(s));

        start_time_s = 0.0;
        if isfield(src, 'start_time_s') && ~isempty(src.start_time_s)
            start_time_s = double(src.start_time_s);
        end
        offset_samples = max(0, round(start_time_s * fs));

        binaural_td = source_binaural.timeData;
        if size(binaural_td, 2) ~= 2
            error('La convolución de la fuente %d no produjo una señal binaural de 2 canales.', s);
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

    mix = zeros(max_total_len, 2);
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
        mix = zeros(0, 2);
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

function batch_summary = build_batch_summary(cfg, runs)
    batch_summary = struct();
    batch_summary.scene_type = 'static';
    batch_summary.scene_id = cfg.scene_id;
    batch_summary.n_variants = numel(runs);
    batch_summary.hrtf_ids = {runs.hrtf_id};
    batch_summary.output_wav_paths = cell(1, numel(runs));
    batch_summary.output_metadata_paths = cell(1, numel(runs));
    for i = 1:numel(runs)
        batch_summary.output_wav_paths{i} = runs(i).output.wav_path;
        batch_summary.output_metadata_paths{i} = runs(i).output.metadata_path;
    end
    batch_summary.effective_seed = cfg.render.effective_seed;
    batch_summary.seed_source = cfg.render.seed_source;
end
