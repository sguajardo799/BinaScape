function write_render_failure(config_path, context, original_error)
    % Evidencia funcional de rechazo de entrada, no constancia de finalización.
    % Contextos anteriores sin failure_path conservan su comportamiento.
    if nargin < 2 || isempty(context), return; end
    if isstruct(context) && isscalar(context) && ~isfield(context, 'failure_path')
        return;
    end
    fields = {'attempt_id', 'variant_id', 'manifest_sha256', 'failure_path'};
    if ~isstruct(context) || ~isscalar(context)
        error('BinaScape:Failure:InvalidContext', 'Contexto funcional no válido.');
    end
    for i = 1:numel(fields)
        name = fields{i};
        if ~isfield(context, name) || ~is_text(context.(name))
            error('BinaScape:Failure:InvalidContext', 'Campo funcional no válido: %s', name);
        end
        context.(name) = char(context.(name));
    end
    if isempty(regexp(context.attempt_id, '^[0-9a-f]{32}$', 'once')) || ...
            isempty(regexp(context.manifest_sha256, '^[0-9a-f]{64}$', 'once'))
        error('BinaScape:Failure:InvalidContext', 'Identidad o huella no válida.');
    end
    % Si la carga no pudo abrir el snapshot, no atribuir bytes no observados.
    if ~strcmp(sha256_file(config_path), context.manifest_sha256)
        error('BinaScape:Failure:ManifestMismatch', 'El manifiesto no coincide con el intento.');
    end
    target_file = java.io.File(context.failure_path);
    if ~target_file.isAbsolute()
        error('BinaScape:Failure:InvalidPath', 'La ruta debe ser absoluta.');
    end
    target_path = char(target_file.getCanonicalPath());
    if isfile(target_path) || isfolder(target_path)
        error('BinaScape:Failure:AlreadyExists', 'El informe ya existe.');
    end
    if isfield(context, 'completion_path') && ...
            (isfile(context.completion_path) || isfolder(context.completion_path))
        error('BinaScape:Failure:Contradiction', 'Ya existe evidencia de finalización.');
    end
    report = struct('schema_version', 1, 'attempt_id', context.attempt_id, ...
        'variant_id', context.variant_id, 'manifest_sha256', context.manifest_sha256, ...
        'category', 'invalid_input', 'error', struct('identifier', original_error.identifier, ...
        'message', original_error.message));
    % No inferir identidad a partir de una entrada que no pasó validación.
    for name = {'scene_id', 'output_type'}
        field = name{1};
        if isfield(context, field)
            if ~is_text(context.(field))
                error('BinaScape:Failure:InvalidContext', 'Campo funcional no válido: %s', field);
            end
            report.(field) = char(context.(field));
        end
    end
    bytes = unicode2native(jsonencode(report, 'PrettyPrint', true), 'UTF-8');
    temporary_path = [target_path '.tmp'];
    fid = fopen(temporary_path, 'wb');
    if fid == -1
        error('BinaScape:Failure:WriteFailed', 'No se pudo abrir: %s', temporary_path);
    end
    try
        if fwrite(fid, bytes, 'uint8') ~= numel(bytes)
            error('BinaScape:Failure:WriteFailed', 'Escritura incompleta del informe.');
        end
        status = fclose(fid);
        fid = -1;
        if status ~= 0
            error('BinaScape:Failure:CloseFailed', 'No se pudo cerrar el informe.');
        end
        if isfile(target_path) || isfolder(target_path)
            error('BinaScape:Failure:AlreadyExists', 'El informe ya existe.');
        end
        options = javaArray('java.nio.file.CopyOption', 1);
        options(1) = java.nio.file.StandardCopyOption.ATOMIC_MOVE;
        java.nio.file.Files.move(java.io.File(temporary_path).toPath(), ...
            java.io.File(target_path).toPath(), options);
    catch ME
        if fid ~= -1
            try, fclose(fid); catch, end
        end
        rethrow(ME);
    end
end

function valid = is_text(value)
    valid = ((ischar(value) && isrow(value)) || ...
        (isstring(value) && isscalar(value) && ~ismissing(value))) && ...
        strlength(strtrim(string(value))) > 0;
end
