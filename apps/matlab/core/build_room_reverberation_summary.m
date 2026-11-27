function reverberation = build_room_reverberation_summary(raw_t30)
    raven_octave_frequencies_hz = [31.5 63 125 250 500 1000 2000 4000 8000 16000];

    reverberation = struct();
    reverberation.metric = 'T30';
    reverberation.estimated_from = 'room';
    reverberation.method = 'rpf.getT30';
    reverberation.t30_s = normalize_numeric_array(raw_t30);
    reverberation.band_frequencies_hz = [];
    reverberation.band_resolution = 'octave';
    reverberation.frequency_mapping = 'center_frequency';
    reverberation.valid_band_count = 0;
    reverberation.mean_t30_s = compute_mean_t30(reverberation.t30_s);
    reverberation.status = 'unavailable';
    reverberation.notes = ['T30 de sala obtenido desde RAVEN tras rpf.run(). ', ...
        'El promedio solo está disponible cuando las diez bandas son finitas y positivas.'];

    if ~isempty(reverberation.t30_s)
        reverberation.t30_s = reshape(reverberation.t30_s, 1, []);
        reverberation.valid_band_count = sum(isfinite(reverberation.t30_s) & (reverberation.t30_s > 0));
        if numel(reverberation.t30_s) == numel(raven_octave_frequencies_hz)
            reverberation.band_frequencies_hz = raven_octave_frequencies_hz;
        else
            reverberation.notes = sprintf('%s Cantidad de bandas inesperada: %d de %d.', ...
                reverberation.notes, numel(reverberation.t30_s), numel(raven_octave_frequencies_hz));
        end
    end

    if ~isempty(reverberation.mean_t30_s)
        reverberation.status = 'estimated';
    end
end

function value = normalize_numeric_array(candidate)
    value = [];
    if isnumeric(candidate) || islogical(candidate)
        value = double(candidate);
    end
end

function mean_t30_s = compute_mean_t30(t30_values)
    mean_t30_s = [];
    if isempty(t30_values)
        return;
    end

    if numel(t30_values) ~= 10 || any(~isfinite(t30_values)) || any(t30_values <= 0)
        return;
    end
    mean_t30_s = mean(t30_values, 'all');
end
