function pose = resolve_motion_pose(motion_cfg, t_s, interpolation_mode)
    if nargin < 3 || isempty(interpolation_mode)
        interpolation_mode = 'linear';
    end

    motion_type = lower(string(motion_cfg.type));

    switch motion_type
        case "static"
            pose = normalize_pose_struct(motion_cfg.pose);

        case "keyframed"
            pose = resolve_keyframed_pose(motion_cfg.keyframes, t_s, interpolation_mode);

        otherwise
            error('Unsupported motion.type: %s', motion_type);
    end
end