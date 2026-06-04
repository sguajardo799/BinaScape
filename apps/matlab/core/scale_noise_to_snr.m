function [scaled_noise, info] = scale_noise_to_snr(reference_audio, noise, snr_db)
    if ~isnumeric(snr_db) || ~isscalar(snr_db) || ~isfinite(snr_db)
        error('snr_db must be a finite numeric scalar.');
    end

    signal_rms = sqrt(mean(reference_audio(:) .^ 2));
    noise_rms = sqrt(mean(noise(:) .^ 2));

    info = struct();
    info.signal_rms = signal_rms;
    info.rms_before_scale = noise_rms;
    info.gain = 0;
    info.skipped = false;
    info.skipped_reason = '';

    if isempty(reference_audio) || signal_rms == 0
        scaled_noise = zeros(size(noise));
        info.skipped = true;
        info.skipped_reason = 'silent_signal';
        return;
    end

    if isempty(noise) || noise_rms == 0
        scaled_noise = zeros(size(noise));
        info.skipped = true;
        info.skipped_reason = 'silent_noise';
        return;
    end

    target_noise_rms = signal_rms / (10 .^ (double(snr_db) / 20));
    info.gain = target_noise_rms / noise_rms;
    scaled_noise = noise .* info.gain;
end
