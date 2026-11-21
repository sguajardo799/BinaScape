function backend = host_runtime_backend()
    % Runtime externo para hash, rutas canónicas y renombrado atómico.
    % Prefiere .NET (Windows, cualquier versión de MATLAB) y cae a Java
    % (Linux/macOS o MATLAB con JVM). MATLAB R2026b ya no incluye JVM.
    persistent cached
    if isempty(cached)
        if ispc && NET.isNETSupported
            cached = 'dotnet';
        elseif usejava('jvm')
            cached = 'java';
        else
            error('BinaScape:Runtime:Unavailable', ...
                ['No hay .NET ni JVM disponibles en esta sesión de MATLAB; ' ...
                 'se requiere uno de los dos para hash y escritura atómica.']);
        end
    end
    backend = cached;
end
