function pose = resolve_keyframed_pose(keyframes, t_s, interpolation_mode)
    n = numel(keyframes);

    times = zeros(n, 1);
    positions = zeros(n, 3);
    ypr = zeros(n, 3);

    for i = 1:n
        kf = keyframes(i);
        times(i) = double(kf.t_s);
        positions(i, :) = double(kf.position_m(:)).';
        ypr(i, 1) = double(kf.orientation_deg.yaw);
        ypr(i, 2) = double(kf.orientation_deg.pitch);
        ypr(i, 3) = double(kf.orientation_deg.roll);
    end

    if t_s <= times(1)
        pos = positions(1, :);
        ang = ypr(1, :);

    elseif t_s >= times(end)
        pos = positions(end, :);
        ang = ypr(end, :);

    else
        switch lower(string(interpolation_mode))
            case "linear"
                pos = interp1(times, positions, t_s, 'linear');
                ang = interp1(times, ypr, t_s, 'linear');

            otherwise
                error('Unsupported interpolation mode: %s', interpolation_mode);
        end
    end

    pose = struct();
    pose.position_m = pos;
    pose.orientation_deg = struct( ...
        'yaw', ang(1), ...
        'pitch', ang(2), ...
        'roll', ang(3) ...
    );
end