function digest_hex = sha256_file(path_value)
    % SHA-256 de bytes exactos; compatible con hashlib de Python.
    % Usa .NET (Windows) o Java según disponibilidad: MATLAB R2026b ya no
    % incluye JVM, y Linux/macOS no tienen .NET. El digest no depende del
    % backend elegido.
    path_value = char(path_value);
    if ~isfile(path_value)
        error('BinaScape:Completion:ReadFailed', 'No se pudo abrir: %s', path_value);
    end
    switch host_runtime_backend()
        case 'dotnet'
            bytes = dotnet_digest(path_value);
        case 'java'
            bytes = java_digest(path_value);
    end
    digest_hex = lower(reshape(dec2hex(bytes, 2).', 1, []));
end

function bytes = dotnet_digest(path_value)
    try
        stream = System.IO.File.OpenRead(path_value);
    catch ME
        error('BinaScape:Completion:ReadFailed', 'No se pudo abrir: %s (%s)', path_value, ME.message);
    end
    cleanup = onCleanup(@() stream.Dispose());
    algorithm = System.Security.Cryptography.SHA256.Create();
    algorithm_cleanup = onCleanup(@() algorithm.Dispose());
    % ComputeHash(Stream) lee por bloques internamente.
    bytes = uint8(algorithm.ComputeHash(stream));
    clear cleanup algorithm_cleanup
end

function bytes = java_digest(path_value)
    fid = fopen(path_value, 'rb');
    if fid == -1
        error('BinaScape:Completion:ReadFailed', 'No se pudo abrir: %s', path_value);
    end
    try
        digest = java.security.MessageDigest.getInstance('SHA-256');
        while true
            block = fread(fid, 1024 * 1024, '*uint8');
            if isempty(block), break; end
            digest.update(typecast(block(:), 'int8'));
        end
        [message, errnum] = ferror(fid);
        % EOF normal también genera ferror (R2025b: -4); usar feof, no un
        % código numérico que puede variar entre versiones del runtime.
        if errnum ~= 0 && ~feof(fid)
            error('BinaScape:Completion:ReadFailed', '%s', message);
        end
        status = fclose(fid);
        fid = -1;
        if status ~= 0
            error('BinaScape:Completion:CloseFailed', 'No se pudo cerrar: %s', path_value);
        end
        bytes = typecast(digest.digest(), 'uint8');
    catch ME
        if fid ~= -1
            try, fclose(fid); catch, end
        end
        rethrow(ME);
    end
end
