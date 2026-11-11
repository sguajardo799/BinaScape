function [position, view_vector, up_vector] = transform_schema2_pose_to_raven(position_m, orientation_deg)
    % Reflect the public right-handed [x,y,z] pose into RAVEN's [x,y,-z]
    % adapter coordinates. The public forward vector follows the canonical
    % convention (spatial-pose-convention): yaw 0 -> +X, positive yaw is a
    % right-hand rotation about +Y, forward = (cos p cos y, sin p, -cos p sin y).
    % The vectors get the same basis change as the position.
    position = double(position_m(:).');
    yaw = deg2rad(double(orientation_deg.yaw));
    pitch = deg2rad(double(orientation_deg.pitch));
    view_vector = [cos(pitch) * cos(yaw), sin(pitch), -cos(pitch) * sin(yaw)];
    up_vector = [0, 1, 0];
    reflection = [1, 1, -1];
    position = position .* reflection;
    view_vector = view_vector .* reflection;
    up_vector = up_vector .* reflection;
end
