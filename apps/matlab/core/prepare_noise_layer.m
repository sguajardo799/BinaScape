function [noise, info] = prepare_noise_layer(layer, fs, target_samples, target_channels, render_seed, layer_index)
    if nargin < 5
        render_seed = [];
    end
    if nargin < 6
        layer_index = 1;
    end

    info = struct();
    info.strategy_original = get_layer_string(layer, 'strategy_original', get_layer_string(layer, 'strategy', ''));
    info.strategy = get_layer_string(layer, 'strategy', '');
    info.snr_db = double(layer.snr_db);
    info.seed = [];
    if isfield(layer, 'seed') && ~isempty(layer.seed)
        info.seed = double(layer.seed);
    end
    info.effective_seed = resolve_layer_seed(render_seed, layer_index, info.seed);
    info.source_fs = fs;
    info.resampled = false;
    info.path = '';
    info.color = '';

    switch info.strategy
        case 'colored'
            info.color = get_layer_string(layer, 'color', 'white');
            raw_noise = generate_colored_noise(info.color, target_samples, info.effective_seed);
            info.source_num_samples = size(raw_noise, 1);
            info.source_num_channels = size(raw_noise, 2);
        case 'audio_file'
            info.path = get_layer_string(layer, 'path', '');
            [raw_noise, source_fs] = audioread(info.path);
            raw_noise = double(raw_noise);
            info.source_fs = double(source_fs);
            if source_fs ~= fs
            if exist('resample', 'file') == 0
                    error('Background noise WAV requires resampling from %d Hz to %d Hz, but resample is not available.', source_fs, fs);
                end
                raw_noise = resample(raw_noise, double(fs), double(source_fs));
                info.resampled = true;
            end
            info.source_num_samples = size(raw_noise, 1);
            info.source_num_channels = size(raw_noise, 2);
        otherwise
            error('Unsupported background noise strategy: %s', info.strategy);
    end

    [noise, fit_info] = fit_noise_to_audio_shape(raw_noise, target_samples, target_channels);
    info.duration_mode = fit_info.duration_mode;
    info.channel_mode = fit_info.channel_mode;
    info.target_num_samples = fit_info.target_num_samples;
    info.target_num_channels = fit_info.target_num_channels;
end

function seed = resolve_layer_seed(render_seed, layer_index, layer_seed)
    if ~isempty(layer_seed)
        seed = double(layer_seed);
    elseif ~isempty(render_seed)
        seed = double(render_seed) + double(layer_index) - 1;
    else
        seed = double(layer_index);
    end
end

function value = get_layer_string(layer, field_name, default_value)
    if isfield(layer, field_name) && ~isempty(layer.(field_name))
        value = char(string(layer.(field_name)));
    else
        value = default_value;
    end
end
