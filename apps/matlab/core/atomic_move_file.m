function atomic_move_file(source_path, target_path)
    % Renombrado atómico en el mismo volumen; falla si el destino existe.
    % No degrada a copiar y borrar.
    source_path = char(source_path);
    target_path = char(target_path);
    switch host_runtime_backend()
        case 'dotnet'
            % File.Move copia entre volúmenes; exigir la misma raíz.
            source_root = char(System.IO.Path.GetPathRoot(System.IO.Path.GetFullPath(source_path)));
            target_root = char(System.IO.Path.GetPathRoot(System.IO.Path.GetFullPath(target_path)));
            if ~strcmpi(source_root, target_root)
                error('BinaScape:Runtime:CrossVolumeMove', ...
                    'Renombrado atómico entre volúmenes no soportado: %s -> %s', ...
                    source_path, target_path);
            end
            % En el mismo volumen File.Move es un MoveFile (rename) y
            % falla si el destino ya existe.
            System.IO.File.Move(source_path, target_path);
        case 'java'
            options = javaArray('java.nio.file.CopyOption', 1);
            options(1) = java.nio.file.StandardCopyOption.ATOMIC_MOVE;
            java.nio.file.Files.move(java.io.File(source_path).toPath(), ...
                java.io.File(target_path).toPath(), options);
    end
end
