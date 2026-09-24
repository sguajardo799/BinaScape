function tests = test_build_receiver_from_pose
    tests = functiontests(localfunctions);
end

function testSchema2ReflectsReceiverPositionAndPublicOrientation(testCase)
    hrtf_path = [tempname, '.daff'];
    fid = fopen(hrtf_path, 'w');
    fclose(fid);
    cleanup = onCleanup(@() delete(hrtf_path)); %#ok<NASGU>

    receiver = struct( ...
        'position_m', [1.0 1.5 2.0], ...
        'orientation_deg', struct('yaw', 90.0, 'pitch', 0.0, 'roll', 0.0), ...
        'active_hrtf', struct('hrtf_path', hrtf_path));
    ctx = struct( ...
        'manifest', struct('schema_version', '2.0', 'receiver', receiver), ...
        'rpf', MockRavenProject());

    ctx = build_receiver_from_pose(ctx);

    verifyEqual(testCase, ctx.rpf.receiver_positions, [1.0 1.5 -2.0], ...
        'AbsTol', 1e-12);
    verifyEqual(testCase, ctx.rpf.receiver_view_vectors, [0.0 0.0 -1.0], ...
        'AbsTol', 1e-12);
end

function testSchema3ReflectsReceiverPositionAndPublicOrientation(testCase)
    hrtf_path = [tempname, '.daff'];
    fid = fopen(hrtf_path, 'w');
    fclose(fid);
    cleanup = onCleanup(@() delete(hrtf_path)); %#ok<NASGU>

    receiver = struct( ...
        'position_m', [1.0 1.5 2.0], ...
        'orientation_deg', struct('yaw', 90.0, 'pitch', 0.0, 'roll', 0.0), ...
        'active_hrtf', struct('hrtf_path', hrtf_path));
    ctx = struct( ...
        'manifest', struct('schema_version', '3.0', 'receiver', receiver), ...
        'rpf', MockRavenProject());

    ctx = build_receiver_from_pose(ctx);

    verifyEqual(testCase, ctx.rpf.receiver_positions, [1.0 1.5 -2.0], ...
        'AbsTol', 1e-12);
    verifyEqual(testCase, ctx.rpf.receiver_view_vectors, [0.0 0.0 -1.0], ...
        'AbsTol', 1e-12);
end

function testLegacyReceiverKeepsExistingCoordinates(testCase)
    hrtf_path = [tempname, '.daff'];
    fid = fopen(hrtf_path, 'w');
    fclose(fid);
    cleanup = onCleanup(@() delete(hrtf_path)); %#ok<NASGU>

    receiver = struct( ...
        'position_m', [1.0 1.5 2.0], ...
        'orientation_deg', struct('yaw', 90.0, 'pitch', 0.0, 'roll', 0.0), ...
        'active_hrtf', struct('hrtf_path', hrtf_path));
    ctx = struct('manifest', struct('receiver', receiver), ...
        'rpf', MockRavenProject());

    ctx = build_receiver_from_pose(ctx);

    verifyEqual(testCase, ctx.rpf.receiver_positions, [1.0 1.5 2.0], ...
        'AbsTol', 1e-12);
    verifyEqual(testCase, ctx.rpf.receiver_view_vectors, [0.0 0.0 1.0], ...
        'AbsTol', 1e-12);
end
