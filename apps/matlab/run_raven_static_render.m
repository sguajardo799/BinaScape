function run_raven_static_render(config_path)
    % Entry point para render estático.
    %
    % Uso esperado:
    %   run_raven_static_render('path/to/scene_static_0001.json')

    addpath(genpath(fileparts(mfilename('fullpath'))));

    fprintf('[INFO] Static render started\n');
    fprintf('[INFO] Config: %s\n', config_path);

    try
        cfg = load_scene_config(config_path, @validate_static_scene_config);
        fprintf('[INFO] Config loaded\n');

        result = render_static_scene(cfg);
        fprintf('[INFO] Static render batch generated (%d variante(s))\n', numel(result.runs));

        for i = 1:numel(result.runs)
            run_result = result.runs(i);
            write_output_wav(run_result.audio, run_result.fs, cfg.render.outputs(i).wav_path);
            export_render_metadata(cfg, run_result, cfg.render.outputs(i).metadata_path, result.summary);
            fprintf('[INFO] Variant %s written to %s\n', run_result.hrtf_id, cfg.render.outputs(i).wav_path);
        end

        fprintf('[INFO] Static render completed successfully\n');
    catch ME
        fprintf(2, '[ERROR] Static render failed: %s\n', ME.message);
        rethrow(ME);
    end
end
