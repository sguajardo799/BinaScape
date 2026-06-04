function [audio_out, metadata] = apply_background_noise(audio_in, fs, background_noise_cfg, run_ctx)
    if nargin < 4
        run_ctx = struct();
    end

    audio_out = audio_in;
    metadata = initialize_metadata(audio_in, background_noise_cfg);

    if ~metadata.enabled
        metadata.skipped_reason = 'disabled';
        return;
    end

    if isempty(audio_in)
        metadata.skipped_reason = 'empty_audio';
        return;
    end

    layers = background_noise_cfg.layers;
    target_samples = size(audio_in, 1);
    target_channels = size(audio_in, 2);
    render_seed = [];
    if isfield(run_ctx, 'effective_seed')
        render_seed = run_ctx.effective_seed;
    end

    accumulated_noise = zeros(size(audio_in));
    layer_metadata_cells = cell(1, numel(layers));
    any_applied = false;

    for i = 1:numel(layers)
        [noise, prep_info] = prepare_noise_layer(layers(i), fs, target_samples, target_channels, render_seed, i);
        [scaled_noise, scale_info] = scale_noise_to_snr(audio_in, noise, layers(i).snr_db);

        layer_info = merge_structs(prep_info, scale_info);
        layer_info.applied = ~scale_info.skipped;
        if scale_info.skipped
            layer_info.skipped_reason = scale_info.skipped_reason;
        else
            any_applied = true;
        end
        layer_metadata_cells{i} = layer_info;
        accumulated_noise = accumulated_noise + scaled_noise;
    end

    audio_out = audio_in + accumulated_noise;
    metadata.layers = [layer_metadata_cells{:}];
    metadata.applied = any_applied;
    if ~any_applied
        metadata.skipped_reason = 'all_layers_skipped';
    end

    metadata.peak_before_guard = max(abs(audio_out), [], 'all');
    metadata.guard_gain = 1.0;
    metadata.guard_limit = 0.999;
    metadata.guard_applied = false;
    metadata.effective_snr_may_change = false;
    if ~isempty(metadata.peak_before_guard) && metadata.peak_before_guard > metadata.guard_limit
        metadata.guard_gain = metadata.guard_limit / metadata.peak_before_guard;
        audio_out = audio_out .* metadata.guard_gain;
        metadata.guard_applied = true;
        metadata.effective_snr_may_change = true;
    end
    metadata.peak_after_guard = max(abs(audio_out), [], 'all');
end

function metadata = initialize_metadata(audio_in, background_noise_cfg)
    metadata = struct();
    metadata.enabled = false;
    metadata.applied = false;
    metadata.skipped_reason = '';
    metadata.signal_rms = sqrt(mean(audio_in(:) .^ 2));
    metadata.peak_before_guard = max(abs(audio_in), [], 'all');
    metadata.peak_after_guard = metadata.peak_before_guard;
    metadata.guard_gain = 1.0;
    metadata.guard_limit = 0.999;
    metadata.guard_applied = false;
    metadata.effective_snr_may_change = false;
    metadata.layers = struct([]);

    if ~isempty(background_noise_cfg) && isstruct(background_noise_cfg) && ...
            isfield(background_noise_cfg, 'enabled') && logical(background_noise_cfg.enabled)
        metadata.enabled = true;
    end
end

function out = merge_structs(a, b)
    out = a;
    names = fieldnames(b);
    for i = 1:numel(names)
        out.(names{i}) = b.(names{i});
    end
end
