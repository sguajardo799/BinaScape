function audio = trim_audio_to_target_duration(audio, fs, target_duration_s)
    if nargin < 3 || isempty(target_duration_s)
        return;
    end

    target_samples = round(double(target_duration_s) * double(fs));
    target_samples = max(1, target_samples);

    current_samples = size(audio, 1);
    if current_samples > target_samples
        audio = audio(1:target_samples, :);
    elseif current_samples < target_samples
        pad_samples = target_samples - current_samples;
        audio = [audio; zeros(pad_samples, size(audio, 2), class(audio))];
    end
end
