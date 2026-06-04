function run_raven_dynamic_render(config_path)
    % Entry point para render dinámico.
    %
    % Uso:
    %   run_raven_dynamic_render('manifests/scene_dynamic_0001.json')

    addpath(genpath(fileparts(mfilename('fullpath'))));

    fprintf('[INFO] Dynamic render started\n');
    fprintf('[INFO] Config: %s\n', config_path);

    try
        cfg = load_scene_config(config_path);
        validate_dynamic_scene_config(cfg);

        result = render_dynamic_scene(cfg);

        write_output_wav(result.audio, result.fs, cfg.render.output_wav_path);
        export_render_metadata(cfg, result, cfg.render.output_metadata_path);

        fprintf('[INFO] Dynamic render completed successfully\n');

    catch ME
        fprintf(2, '[ERROR] Dynamic render failed: %s\n', ME.message);
        rethrow(ME);
    end
end