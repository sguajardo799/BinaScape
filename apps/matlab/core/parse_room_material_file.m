function material = parse_room_material_file(material_path)
    material_path = char(string(material_path));

    if ~isfile(material_path)
        error('Room material file not found: %s', material_path);
    end

    raw_text = fileread(material_path);
    lines = regexp(raw_text, '\r\n|\n|\r', 'split');

    parsed = struct();
    for i = 1:numel(lines)
        line = strtrim(lines{i});
        if isempty(line) || startsWith(line, '#') || startsWith(line, ';') || startsWith(line, '[')
            continue;
        end

        tokens = regexp(line, '^(?<key>[A-Za-z0-9_]+)\s*=\s*(?<value>.*)$', 'names', 'once');
        if isempty(tokens)
            error('Invalid material file line in %s: %s', material_path, line);
        end

        key = lower(strtrim(tokens.key));
        value = strtrim(tokens.value);

        switch key
            case {'absorp', 'scatter', 'interpol'}
                parsed.(key) = parse_numeric_vector(value, key, material_path);
            otherwise
                parsed.(key) = value;
        end
    end

    if ~isfield(parsed, 'absorp') || isempty(parsed.absorp)
        error('Material file %s must define absorp.', material_path);
    end
    if ~isfield(parsed, 'scatter') || isempty(parsed.scatter)
        error('Material file %s must define scatter.', material_path);
    end

    validateattributes(parsed.absorp, {'double'}, {'row', 'numel', 31, 'finite', '>=', 0, '<=', 1}, ...
        mfilename, 'absorp');
    validateattributes(parsed.scatter, {'double'}, {'row', 'numel', 31, 'finite', '>=', 0, '<=', 1}, ...
        mfilename, 'scatter');

    material = struct();
    material.name = get_string_field(parsed, 'name', '');
    material.notes = get_string_field(parsed, 'notes', '');
    material.absorp = parsed.absorp;
    material.scatter = parsed.scatter;
    material.material_path = material_path;

    if isfield(parsed, 'interpol')
        material.interpol = parsed.interpol;
    else
        material.interpol = [];
    end
end

function values = parse_numeric_vector(raw_value, field_name, material_path)
    parts = regexp(strtrim(raw_value), '[,\s]+', 'split');
    parts = parts(~cellfun('isempty', parts));
    values = zeros(1, numel(parts));

    for i = 1:numel(parts)
        values(i) = str2double(parts{i});
    end

    if any(isnan(values))
        error('Material file %s has non-numeric %s values.', material_path, field_name);
    end
end

function value = get_string_field(parsed, field_name, default_value)
    if isfield(parsed, field_name)
        value = char(string(parsed.(field_name)));
    else
        value = default_value;
    end
end
