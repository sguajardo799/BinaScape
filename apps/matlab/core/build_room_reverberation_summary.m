function reverberation = build_room_reverberation_summary(raw_t30)
    reverberation = struct();
    reverberation.metric = 'T30';
    reverberation.estimated_from = 'room';
    reverberation.method = 'rpf.getT30';
    reverberation.t30_s = normalize_numeric_array(raw_t30);
    reverberation.mean_t30_s = compute_mean_t30(reverberation.t30_s);
    reverberation.status = 'unavailable';
    reverberation.notes = ['T30 de sala obtenido desde RAVEN tras rpf.run(). ', ...
        'El promedio se calcula usando únicamente valores finitos mayores a cero.'];

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

    valid_values = t30_values(isfinite(t30_values) & (t30_values > 0));
    if isempty(valid_values)
        return;
    end

    mean_t30_s = mean(valid_values, 'all');
end
