function audio = normalize_audio_peak(audio, target_peak)
    if nargin < 2 || isempty(target_peak)
        target_peak = 0.99;
    end

    peak = max(abs(audio), [], 'all');
    if peak <= 0
        return;
    end

    audio = double(audio) * (double(target_peak) / double(peak));
end
