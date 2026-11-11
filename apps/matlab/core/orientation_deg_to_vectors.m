function [viewVec, upVec] = orientation_deg_to_vectors(pitch, yaw)
    % Convierte yaw/pitch/roll en grados a:
    %   - vector de vista
    %   - vector up
    %
    % Uso exclusivo del camino heredado (manifiestos sin esquema 2.0/3.0):
    % devuelve (cos p cos y, sin p, +cos p sin y) directamente en
    % coordenadas RAVEN. Los esquemas 2.0/3.0 usan
    % transform_schema2_pose_to_raven, que aplica la convención pública
    % canónica (spatial-pose-convention) antes de reflejar Z.

    yaw = deg2rad(double(yaw));
    pitch = deg2rad(double(pitch));

    % View vector a partir de yaw/pitch
    vx = cos(pitch) * cos(yaw);
    vy = sin(pitch);
    vz = cos(pitch) * sin(yaw);

    viewVec = [vx, vy, vz];

    % Up vector base
    upVec = [0, 1, 0];

    % Normalización por seguridad
    viewNorm = norm(viewVec);
    if viewNorm < 1e-12
        error('View vector degenerado al convertir orientación.');
    end
    viewVec = viewVec / viewNorm;

    upNorm = norm(upVec);
    upVec = upVec / upNorm;
end