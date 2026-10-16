function tests = test_build_circular_array_receiver
    tests = functiontests(localfunctions);
end

function testYawOnlyPositionsAndRIRSelection(testCase)
    for yaw = [0 90]
        center = [2 1.5 2];
        microphones = repmat(struct('microphone_index', 0, 'channel_index', 0, 'position_m', []), 1, 4);
        positions = zeros(4, 3);
        for k = 1:4
            positions(k,:) = center + 0.05 * [cosd(yaw+90*(k-1)) 0 sind(yaw+90*(k-1))];
            microphones(k) = struct('microphone_index', k-1, 'channel_index', k, 'position_m', positions(k,:));
        end
        array = struct('output_type', 'circular_array', 'num_microphones', 4, ...
            'radius_m', 0.05, 'center_m', center, 'yaw_deg', yaw, 'microphones', microphones);
        receiver = struct('position_m', center, 'orientation_deg', struct('yaw', yaw, 'pitch', 45, 'roll', 60), ...
            'circular_array', array);
        ctx = struct('manifest', struct('schema_version', '3.0', 'receiver', receiver), 'rpf', MockRavenProject());
        ctx = build_circular_array_receiver(ctx);
        verifyEqual(testCase, ctx.rpf.receiver_positions, positions .* [1 1 -1], 'AbsTol', 1e-10);
        verifyEqual(testCase, ctx.rpf.receiver_positions(:,2), repmat(1.5,4,1));
        verifyEqual(testCase, ctx.rpf.receiver_names, {'mic_0000','mic_0001','mic_0002','mic_0003'});
        verifyEmpty(testCase, ctx.rpf.receiver_hrtf);
        verifyEqual(testCase, ctx.rpf.generate_rir, 1);
        verifyEqual(testCase, ctx.rpf.generate_brir, 0);
    end
end
