function digest_hex = sha256_file(path_value)
    % SHA-256 de bytes exactos por bloques; compatible con hashlib de Python.
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
        digest_hex = lower(reshape(dec2hex(bytes, 2).', 1, []));
    catch ME
        if fid ~= -1
            try, fclose(fid); catch, end
        end
        rethrow(ME);
    end
end
