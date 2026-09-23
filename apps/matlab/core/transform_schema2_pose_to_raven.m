function [position, view_vector, up_vector] = transform_schema2_pose_to_raven(position_m, orientation_deg)
    % Reflect the public right-handed [x,y,z] pose into RAVEN's [x,y,-z]
    % adapter coordinates. Transforming the vectors explicitly keeps the
    % orientation coupled to the same basis change as the position.
    position = double(position_m(:).');
    [view_vector, up_vector] = orientation_deg_to_vectors( ...
        orientation_deg.pitch, orientation_deg.yaw);
    reflection = [1, 1, -1];
    position = position .* reflection;
    view_vector = view_vector .* reflection;
    up_vector = up_vector .* reflection;
end
