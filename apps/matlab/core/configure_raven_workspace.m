function workspace = configure_raven_workspace(rpf, project_name, root)
    % Crea un workspace privado por proyecto RAVEN y apunta la base de
    % materiales del proyecto a él.
    %
    % itaRavenProject.setMaterial escribe <pathMaterials>\<slot>.mat y
    % setModelToFaces escribe room_<N>_faces. Con las rutas globales por
    % defecto, renders concurrentes de escenas distintas se sobrescriben
    % materiales y geometría. Cada proyecto usa aquí sus propias carpetas.
    if nargin < 3 || isempty(root)
        root = fullfile('C:\ITASoftware\Raven\RavenInput', 'binascape_ws');
    end
    project_name = char(string(project_name));

    workspace = struct();
    workspace.root = fullfile(char(string(root)), project_name);
    workspace.material_database_path = fullfile(workspace.root, 'MaterialDatabase');
    workspace.models_path = fullfile(workspace.root, 'RavenModels');

    % Restos de un intento anterior del mismo proyecto no deben reutilizarse.
    if isfolder(workspace.root)
        rmdir(workspace.root, 's');
    end
    mkdir(workspace.material_database_path);
    mkdir(workspace.models_path);

    rpf.setPathMaterials(workspace.material_database_path);
end
