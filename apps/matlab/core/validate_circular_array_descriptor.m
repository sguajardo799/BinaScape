function array = validate_circular_array_descriptor(array, center, yaw)
    % Valida posiciones públicas y el orden estable micrófono/canal.
    required = {'output_type', 'num_microphones', 'radius_m', 'center_m', 'yaw_deg', 'microphones'};
    for i = 1:numel(required)
        if ~isstruct(array) || ~isscalar(array) || ~isfield(array, required{i})
            error('BinaScape:Matlab:InvalidArray', 'circular_array.%s es obligatorio.', required{i});
        end
    end
    n = array.num_microphones;
    r = array.radius_m;
    if ~isnumeric(n) || ~isscalar(n) || ~isfinite(n) || n < 2 || n ~= fix(n) || ...
            ~isnumeric(r) || ~isscalar(r) || ~isfinite(r) || r <= 0
        error('BinaScape:Matlab:InvalidArray', 'El array requiere N entero >=2 y radio finito positivo.');
    end
    if ~strcmp(char(string(array.output_type)), 'circular_array') || ...
            ~isnumeric(array.center_m) || numel(array.center_m) ~= 3 || ...
            any(~isfinite(array.center_m)) || ~isscalar(array.yaw_deg) || ...
            ~isnumeric(array.yaw_deg) || ~isfinite(array.yaw_deg) || ...
            ~isscalar(yaw) || ~isfinite(yaw)
        error('BinaScape:Matlab:InvalidArray', 'Identidad o pose de array inválida.');
    end
    array.center_m = double(array.center_m(:).');
    center = double(center(:).');
    if numel(center) ~= 3 || any(~isfinite(center)) || ...
            any(abs(array.center_m - center) > 1e-6) || ...
            abs(array.yaw_deg - yaw) > 1e-6
        error('BinaScape:Matlab:InvalidArray', 'El centro/yaw del array no coincide con el receptor.');
    end
    if iscell(array.microphones)
        array.microphones = [array.microphones{:}];
    end
    if ~isstruct(array.microphones) || numel(array.microphones) ~= n
        error('BinaScape:Matlab:InvalidArray', 'Cantidad de micrófonos incorrecta.');
    end
    array.microphones = array.microphones(:).';
    for k = 1:n
        mic = array.microphones(k);
        if ~all(isfield(mic, {'microphone_index', 'channel_index', 'position_m'})) || ...
                ~isequal(mic.microphone_index, k-1) || ~isequal(mic.channel_index, k) || ...
                ~isnumeric(mic.position_m) || numel(mic.position_m) ~= 3 || ...
                any(~isfinite(mic.position_m))
            error('BinaScape:Matlab:InvalidArray', 'Micrófono/canal inválido en posición %d.', k);
        end
        theta = deg2rad(yaw + 360 * (k-1) / n);
        expected = center + r * [cos(theta), 0, sin(theta)];
        position = double(mic.position_m(:).');
        if any(abs(position - expected) > 1e-6)
            error('BinaScape:Matlab:InvalidArray', 'Posición de micrófono %d inconsistente.', k-1);
        end
        array.microphones(k).position_m = position;
    end
end
