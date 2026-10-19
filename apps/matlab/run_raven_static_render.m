function run_raven_static_render(config_path, diagnostics)
    % Entry point para render estático.
    %
    % Uso esperado:
    %   run_raven_static_render('path/to/scene_static_0001.json')

    addpath(genpath(fileparts(mfilename('fullpath'))));
    if nargin < 2
        diagnostics = [];
    end

    fprintf('[INFO] Static render started\n');
    fprintf('[INFO] Config: %s\n', config_path);

    try
        emit_render_event(diagnostics, 'stage_started', 'initialization');
        if isstruct(diagnostics) && isscalar(diagnostics)
            diagnostics.initialization_started = true;
        end
        cfg = load_scene_config(config_path, @validate_static_scene_config);
        fprintf('[INFO] Config loaded\n');

        result = render_static_scene(cfg, diagnostics);
        fprintf('[INFO] Static render batch generated (%d variante(s))\n', numel(result.runs));

        emit_render_event(diagnostics, 'stage_started', 'artifact_writing');
        for i = 1:numel(result.runs)
            run_result = result.runs(i);
            write_output_wav(run_result.audio, run_result.fs, cfg.render.outputs(i).wav_path);
            emit_render_event(diagnostics, 'artifact_written', 'artifact_writing', ...
                struct('output_type', get_render_output_type(run_result), ...
                'path', cfg.render.outputs(i).wav_path, 'kind', 'wav'));
            export_render_metadata(cfg, run_result, cfg.render.outputs(i).metadata_path, result.batch);
            emit_render_event(diagnostics, 'artifact_written', 'artifact_writing', ...
                struct('output_type', get_render_output_type(run_result), ...
                'path', cfg.render.outputs(i).metadata_path, 'kind', 'metadata'));
            fprintf('[INFO] Variant %s written to %s\n', get_render_output_type(run_result), cfg.render.outputs(i).wav_path);
        end
        emit_render_event(diagnostics, 'stage_completed', 'artifact_writing');

        fprintf('[INFO] Static render completed successfully\n');
        emit_render_event(diagnostics, 'entrypoint_completed', '');
    catch ME
        emit_render_event(diagnostics, 'error', '', struct('error', ...
            struct('identifier', ME.identifier, 'message', ME.message, 'stack', ME.stack)));
        fprintf(2, '[ERROR] Static render failed: %s\n', ME.message);
        rethrow(ME);
    end
end
