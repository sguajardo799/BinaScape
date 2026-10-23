function write_render_completion(config_path, cfg, context)
    % Constancia funcional: errores fatales, nunca diagnóstico de mejor esfuerzo.
    if nargin < 3 || isempty(context), return; end
    fields = {'attempt_id', 'variant_id', 'manifest_sha256', 'completion_path'};
    if ~isstruct(context) || ~isscalar(context)
        error('BinaScape:Completion:InvalidContext', 'Contexto funcional no válido.');
    end
    for i = 1:numel(fields)
        name = fields{i};
        if ~isfield(context, name) || ~(ischar(context.(name)) || ...
                (isstring(context.(name)) && isscalar(context.(name)))) || isempty(context.(name))
            error('BinaScape:Completion:InvalidContext', 'Campo funcional no válido: %s', name);
        end
        context.(name) = char(context.(name));
    end
    if isempty(regexp(context.attempt_id, '^[0-9a-f]{32}$', 'once')) || ...
            isempty(regexp(context.manifest_sha256, '^[0-9a-f]{64}$', 'once'))
        error('BinaScape:Completion:InvalidContext', 'Identidad o huella no válida.');
    end
    actual_manifest_hash = sha256_file(config_path);
    if ~strcmp(actual_manifest_hash, context.manifest_sha256)
        error('BinaScape:Completion:ManifestMismatch', 'El manifiesto no coincide con el intento.');
    end
    completion_path = absolute_path(context.completion_path);
    if isfile(completion_path) || isfolder(completion_path)
        error('BinaScape:Completion:AlreadyExists', 'La constancia ya existe: %s', completion_path);
    end
    outputs = cfg.render.outputs;
    if isempty(outputs)
        error('BinaScape:Completion:InvalidInventory', 'No hay salidas esperadas.');
    end
    entries = repmat(struct('kind', '', 'path', '', 'size_bytes', 0, 'sha256', ''), 1, 2*numel(outputs));
    kinds = {'wav', 'metadata'};
    output_fields = {'wav_path', 'metadata_path'};
    for i = 1:numel(outputs)
        for j = 1:2
            path_value = absolute_path(outputs(i).(output_fields{j}));
            if ~isfile(path_value)
                error('BinaScape:Completion:MissingArtifact', 'Falta salida: %s', path_value);
            end
            info = dir(path_value);
            entries(2*(i-1)+j) = struct('kind', kinds{j}, 'path', path_value, ...
                'size_bytes', info.bytes, 'sha256', sha256_file(path_value));
        end
    end
    paths = {entries.path};
    if ispc, paths = lower(string(paths)); end
    if numel(unique(paths)) ~= numel(paths)
        error('BinaScape:Completion:InvalidInventory', 'Las rutas de salida no son distintas.');
    end
    output_type = 'batch';
    if numel(outputs) == 1
        if isfield(outputs, 'output_type') && ~isempty(outputs.output_type)
            output_type = outputs.output_type;
        else
            output_type = outputs.hrtf_id;
        end
    end
    receipt = struct('schema_version', 1, 'attempt_id', context.attempt_id, ...
        'scene_id', cfg.scene_id, 'variant_id', context.variant_id, ...
        'output_type', output_type, 'manifest_sha256', actual_manifest_hash, ...
        'artifacts', entries);
    bytes = unicode2native(jsonencode(receipt, 'PrettyPrint', true), 'UTF-8');
    temporary_path = [completion_path '.tmp'];
    % El directorio funcional debe haber sido preparado antes del lanzamiento.
    fid = fopen(temporary_path, 'wb');
    if fid == -1
        error('BinaScape:Completion:WriteFailed', 'No se pudo abrir: %s', temporary_path);
    end
    try
        count = fwrite(fid, bytes, 'uint8');
        if count ~= numel(bytes)
            error('BinaScape:Completion:WriteFailed', 'Escritura incompleta de constancia.');
        end
        status = fclose(fid);
        fid = -1;
        if status ~= 0
            error('BinaScape:Completion:CloseFailed', 'No se pudo cerrar la constancia.');
        end
        if isfile(completion_path) || isfolder(completion_path)
            error('BinaScape:Completion:AlreadyExists', 'La constancia ya existe.');
        end
        % Solicitar atomicidad al sistema; no degradar a copiar y borrar.
        source = java.io.File(temporary_path).toPath();
        target = java.io.File(completion_path).toPath();
        options = javaArray('java.nio.file.CopyOption', 1);
        options(1) = java.nio.file.StandardCopyOption.ATOMIC_MOVE;
        java.nio.file.Files.move(source, target, options);
    catch ME
        if fid ~= -1
            try, fclose(fid); catch, end
        end
        rethrow(ME);
    end
end

function value = absolute_path(value)
    value = char(value);
    file = java.io.File(value);
    if ~file.isAbsolute()
        error('BinaScape:Completion:InvalidPath', 'La ruta debe ser absoluta: %s', value);
    end
    value = strrep(char(file.getCanonicalPath()), '\', '/');
end
