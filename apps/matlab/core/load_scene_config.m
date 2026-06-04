function cfg = load_scene_config(config_path, validator)
    % Carga un archivo JSON, lo convierte a struct MATLAB y opcionalmente
    % aplica una función de validación/normalización.

    if ~isfile(config_path)
        error('Config file not found: %s', config_path);
    end

    txt = fileread(config_path);
    cfg = jsondecode(txt);

    if nargin >= 2 && ~isempty(validator)
        cfg = validator(cfg);
    end
end
