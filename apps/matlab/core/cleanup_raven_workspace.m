function cleanup_raven_workspace(workspace)
    % Elimina el workspace privado de un proyecto RAVEN (best-effort).
    % Un fallo de limpieza solo emite un warning y no altera el render.
    if ~isstruct(workspace) || ~isfield(workspace, 'root') || isempty(workspace.root)
        return;
    end
    root = char(string(workspace.root));
    if ~exist(root, 'file') && ~isfolder(root)
        return;
    end
    try
        rmdir(root, 's');
    catch cleanup_error
        warning('BinaScape:Matlab:RavenWorkspaceCleanupFailed', ...
            'No se pudo eliminar el workspace RAVEN %s: %s', root, cleanup_error.message);
    end
end
