function audio_ita = resample_ita_audio_if_needed(audio_ita, target_fs, source_index, resample_fn)
    if nargin < 3 || isempty(source_index)
        source_index = 0;
    end

    source_fs = double(audio_ita.samplingRate);
    target_fs = double(target_fs);

    if source_fs == target_fs
        return;
    end

    if nargin >= 4 && ~isempty(resample_fn)
        audio_ita = resample_fn(audio_ita, target_fs);
    else
        audio_ita = ita_resample(audio_ita, target_fs);
    end

    if double(audio_ita.samplingRate) ~= target_fs
        if source_index > 0
            error('BinaScape:Matlab:ResampleFailed', ...
                'La fuente %d no pudo re-muestrearse de %d Hz a %d Hz con ita_resample.', ...
                source_index, source_fs, target_fs);
        end

        error('BinaScape:Matlab:ResampleFailed', ...
            'El audio no pudo re-muestrearse de %d Hz a %d Hz con ita_resample.', ...
            source_fs, target_fs);
    end
end
