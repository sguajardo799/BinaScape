function verify_circular_array_render(scene_path, output_dir)
    % Integración opt-in con RAVEN real y fuentes mono/estéreo sintéticas.
    if ~isfolder(output_dir)
        mkdir(output_dir);
    end
    original = jsondecode(fileread(scene_path));
    fs = 44100;
    source = zeros(round(0.2*fs),1);
    source(1) = 0.5;
    mono_path = fullfile(output_dir, 'source_mono.wav');
    stereo_path = fullfile(output_dir, 'source_stereo.wav');
    audiowrite(mono_path, source, fs);
    audiowrite(stereo_path, [source 0.5*source], fs);
    for n = [2 4 8]
        for input_channels = [1 2]
            cfg = original;
            cfg.project_name = sprintf('ArrayRenderSmoke_%d_%d', n, input_channels);
            cfg.receiver.hrtfs = struct([]);
            cfg.receiver.circular_array.num_microphones = n;
            center = cfg.receiver.position_m(:).';
            yaw = cfg.receiver.orientation_deg.yaw;
            microphones = repmat(struct('microphone_index', 0, 'channel_index', 0, 'position_m', []),1,n);
            for k = 1:n
                theta = yaw + 360*(k-1)/n;
                microphones(k) = struct('microphone_index', k-1, 'channel_index', k, ...
                    'position_m', center + cfg.receiver.circular_array.radius_m*[cosd(theta),0,sind(theta)]);
            end
            cfg.receiver.circular_array.microphones = microphones;
            cfg.render.target_duration_s = 0.5;
            cfg.render.active_output_type = 'circular_array';
            for s = 1:numel(cfg.sources)
                if input_channels == 1
                    cfg.sources(s).audio_path = mono_path;
                else
                    cfg.sources(s).audio_path = stereo_path;
                end
                cfg.sources(s).gain_db = -20;
                cfg.sources(s).start_time_s = 0.1*(s-1);
            end
            cfg = validate_static_scene_config(cfg);
            result = render_static_scene(cfg);
            assert(isequal(size(result.audio), [round(0.5*fs),n]), 'Forma/duración del array incorrecta.');
            assert(all(isfinite(result.audio),'all') && any(result.audio ~= 0,'all'), 'Audio inválido.');
            assert(max(abs(result.audio),[],'all') <= 0.999, 'Clipping no protegido.');
            assert(result.summary.sources(1).original_num_channels == input_channels);
            if input_channels == 2
                assert(strcmp(result.summary.sources(1).downmix_policy, 'arithmetic_mean'));
            else
                assert(strcmp(result.summary.sources(1).downmix_policy, 'none'));
            end
            assert(result.summary.sources(1).gain_db == -20);
            assert(result.summary.sources(2).source_start_time_s == 0.1);
            assert(numel(result.summary.room.reverberation.t30_s) == 10);
            fprintf('Array render passed: N=%d, source channels=%d\n', n, input_channels);
        end
    end
end
