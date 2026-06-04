function [noise, info] = fit_noise_to_audio_shape(noise, target_samples, target_channels)
    if nargin < 3
        error('fit_noise_to_audio_shape requires noise, target_samples, and target_channels.');
    end

    target_samples = round(double(target_samples));
    target_channels = round(double(target_channels));
    if target_samples < 0 || target_channels < 1
        error('target_samples must be non-negative and target_channels must be positive.');
    end

    source_samples = size(noise, 1);
    source_channels = size(noise, 2);

    info = struct();
    info.source_num_samples = source_samples;
    info.source_num_channels = source_channels;
    info.target_num_samples = target_samples;
    info.target_num_channels = target_channels;
    info.duration_mode = 'exact';
    info.channel_mode = 'exact';

    if source_samples == 0 && target_samples > 0
        error('Cannot fit empty noise to a non-empty target.');
    end

    if target_samples == 0
        noise = zeros(0, target_channels);
        info.duration_mode = 'trim';
    elseif source_samples < target_samples
        repeats = ceil(target_samples / source_samples);
        noise = repmat(noise, repeats, 1);
        noise = noise(1:target_samples, :);
        info.duration_mode = 'loop';
    elseif source_samples > target_samples
        noise = noise(1:target_samples, :);
        info.duration_mode = 'trim';
    end

    if source_channels == 1 && target_channels > 1
        noise = repmat(noise, 1, target_channels);
        info.channel_mode = 'mono_replicated';
    elseif source_channels == target_channels
        info.channel_mode = 'channel_to_channel';
    else
        error('Noise channel count (%d) is incompatible with target channel count (%d).', source_channels, target_channels);
    end
end
