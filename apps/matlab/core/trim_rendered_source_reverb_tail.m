function audio = trim_rendered_source_reverb_tail(audio, filter_length)
    tail_samples = max(0, round(double(filter_length)) - 1);

    if tail_samples == 0 || isempty(audio)
        return;
    end

    keep_samples = max(0, size(audio, 1) - tail_samples);
    audio = audio(1:keep_samples, :);
end
