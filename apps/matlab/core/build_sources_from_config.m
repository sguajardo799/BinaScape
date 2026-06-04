function ctx = build_sources_from_config(ctx)
    % Construye múltiples fuentes a partir de ctx.cfg.sources
    % y las registra en el proyecto RAVEN.
    %
    % Requiere:
    %   ctx.manifest.sources
    %   ctx.rpf
    %
    % Salida:
    %   ctx.sources           -> struct array con metadata de cada fuente
    %   ctx.sourceNames       -> cell array de nombres
    %   ctx.sourcePositions   -> [N x 3]
    %   ctx.sourceViewVectors -> [N x 3]
    %   ctx.sourceUpVectors   -> [N x 3]

    cfg = ctx.manifest;

    n_sources = numel(cfg.sources);

    sourceNames = cell(1, n_sources);
    sourcePositions = zeros(n_sources, 3);
    sourceViewVectors = zeros(n_sources, 3);
    sourceUpVectors = zeros(n_sources, 3);

    sources(1, n_sources) = struct( ...
        'source_id', '', ...
        'event_type', '', ...
        'audio_path', '', ...
        'directivity_path', '', ...
        'position_m', [], ...
        'orientation_deg', struct('yaw', 0, 'pitch', 0, 'roll', 0), ...
        'view_vector', [], ...
        'up_vector', [], ...
        'gain_db', 0.0, ...
        'start_time_s', 0.0 ...
    );

    sourceDirectivityPaths = cell(1, n_sources);

    for i = 1:n_sources
        src_cfg = cfg.sources(i);

        % Posición
        pos = double(src_cfg.position_m(:)).';

        % Orientación -> vectores
        ori = src_cfg.orientation_deg;
        [viewVec, upVec] = orientation_deg_to_vectors(src_cfg.orientation_deg.pitch, src_cfg.orientation_deg.yaw);

        % Nombre visible en RAVEN
        src_name = char(string(src_cfg.source_id));

        % Campos opcionales
        start_time_s = 0.0;
        if isfield(src_cfg, 'start_time_s') && ~isempty(src_cfg.start_time_s)
            start_time_s = double(src_cfg.start_time_s);
        end

        event_type = "";
        if isfield(src_cfg, 'event_type') && ~isempty(src_cfg.event_type)
            event_type = string(src_cfg.event_type);
        end

        audio_path = "";
        if isfield(src_cfg, 'audio_path') && ~isempty(src_cfg.audio_path)
            audio_path = string(src_cfg.audio_path);
        end

        directivity_path = "";
        if isfield(src_cfg, 'directivity_path') && ~isempty(src_cfg.directivity_path)
            directivity_path = string(src_cfg.directivity_path);
        end

        gain_db = 0.0;
        if isfield(src_cfg, 'gain_db') && ~isempty(src_cfg.gain_db)
            gain_db = double(src_cfg.gain_db);
        end

        % Guardar metadata estructurada
        sources(i).source_id = char(string(src_cfg.source_id));
        sources(i).event_type = char(event_type);
        sources(i).audio_path = char(audio_path);
        sources(i).directivity_path = char(directivity_path);
        sources(i).position_m = pos;
        sources(i).orientation_deg = ori;
        sources(i).view_vector = viewVec;
        sources(i).up_vector = upVec;
        sources(i).gain_db = gain_db;
        sources(i).start_time_s = start_time_s;

        % Acumular para RAVEN
        sourceNames{i} = src_name;
        sourcePositions(i, :) = pos;
        sourceViewVectors(i, :) = viewVec;
        sourceUpVectors(i, :) = upVec;
        sourceDirectivityPaths{i} = char(directivity_path);
    end

    % Registrar en RAVEN
    ctx.rpf.setSourceNames(sourceNames);
    ctx.rpf.setSourcePositions(sourcePositions);
    ctx.rpf.setSourceViewVectors(sourceViewVectors);
    ctx.rpf.setSourceUpVectors(sourceUpVectors);

    for i = 1:n_sources
        directivity_path = sourceDirectivityPaths{i};
        if ~isempty(directivity_path)
            ctx.rpf.setSourceDirectivity(directivity_path);
        end
    end

    % Persistir en contexto
    ctx.sources = sources;
    ctx.sourceNames = sourceNames;
    ctx.sourcePositions = sourcePositions;
    ctx.sourceViewVectors = sourceViewVectors;
    ctx.sourceUpVectors = sourceUpVectors;
    ctx.sourceDirectivityPaths = sourceDirectivityPaths;
    ctx.n_sources = n_sources;
end
