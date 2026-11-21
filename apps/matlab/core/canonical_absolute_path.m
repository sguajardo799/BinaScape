function [canonical, is_absolute] = canonical_absolute_path(value)
    % Ruta normalizada (sin '.', '..' ni separadores redundantes) y si la
    % entrada era absoluta. Con .NET no se resuelven symlinks ni mayúsculas,
    % a diferencia de Java getCanonicalPath; las comparaciones de rutas en
    % Windows ya se hacen sin distinguir mayúsculas.
    value = char(value);
    switch host_runtime_backend()
        case 'dotnet'
            % IsPathRooted acepta '\x' sin unidad; exigir unidad o UNC como Java.
            is_absolute = ~isempty(regexp(value, '^([A-Za-z]:[\\/]|[\\/]{2}[^\\/])', 'once'));
            canonical = char(System.IO.Path.GetFullPath(value));
        case 'java'
            file = java.io.File(value);
            is_absolute = logical(file.isAbsolute());
            canonical = char(file.getCanonicalPath());
    end
end
