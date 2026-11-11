function summary = build_raven_workspace_summary(ctx)
    % Rutas privadas que RAVEN usó en este render, para trazabilidad.
    % model_path queda vacío cuando el modelo no se genera en el workspace
    % (sala shoebox legacy, cuyo modelo depende solo de sus dimensiones).
    summary = struct('material_database_path', '', 'model_path', '');
    if isfield(ctx, 'workspace') && isstruct(ctx.workspace) ...
            && isfield(ctx.workspace, 'material_database_path')
        summary.material_database_path = char(string(ctx.workspace.material_database_path));
    end
    if isfield(ctx, 'raven_model_path') && ~isempty(ctx.raven_model_path)
        summary.model_path = char(string(ctx.raven_model_path));
    end
end
