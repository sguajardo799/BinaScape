function tests = test_build_room_from_config
    tests = functiontests(localfunctions);
end

function testMapsCanonicalShoeboxSlotsByIdentityRegardlessOfReturnedOrder(testCase)
    [room, cleanup] = make_distinct_room_cfg(); %#ok<ASGLU>
    ctx = struct();
    ctx.manifest = struct('room', room);
    ctx.rpf = MockRavenProject({ ...
        'matShoebox4', 'matShoebox1', 'matShoebox6', ...
        'matShoebox2', 'matShoebox5', 'matShoebox3'});

    ctx = build_room_from_config(ctx);

    expected_surfaces = {'floor', 'ceiling', 'south_wall', ...
        'west_wall', 'north_wall', 'east_wall'};
    expected_slots = {'matShoebox1', 'matShoebox2', 'matShoebox3', ...
        'matShoebox4', 'matShoebox5', 'matShoebox6'};
    expected_absorption = [0.5 0.6 0.2 0.4 0.1 0.3];

    verifyEqual(testCase, ctx.rpf.shoebox_dims, [4.2 3.6 2.8]);
    verifyEqual(testCase, numel(ctx.rpf.material_calls), 6);
    verifyEqual(testCase, {ctx.applied_room_materials.surface}, expected_surfaces);
    verifyEqual(testCase, {ctx.applied_room_materials.slot_name}, expected_slots);
    verifyEqual(testCase, {ctx.rpf.material_calls.slot_name}, expected_slots);
    for iSlot = 1:numel(expected_slots)
        verifyEqual(testCase, ctx.rpf.material_calls(iSlot).absorp, ...
            repmat(expected_absorption(iSlot), 1, 31), 'AbsTol', 1e-12);
    end
end

function testRejectsMissingCanonicalShoeboxSlot(testCase)
    [room, cleanup] = make_distinct_room_cfg(); %#ok<ASGLU>
    ctx = struct('manifest', struct('room', room), ...
        'rpf', MockRavenProject({'matShoebox1', 'matShoebox2', 'matShoebox3', ...
            'matShoebox4', 'matShoebox5'}));

    verifyError(testCase, @() build_room_from_config(ctx), ...
        'BinaScape:Matlab:MissingRoomMaterialSlot');
end

function testRejectsDuplicateCanonicalShoeboxSlot(testCase)
    [room, cleanup] = make_distinct_room_cfg(); %#ok<ASGLU>
    ctx = struct('manifest', struct('room', room), ...
        'rpf', MockRavenProject({'matShoebox1', 'matShoebox2', 'matShoebox3', ...
            'matShoebox4', 'matShoebox5', 'matShoebox5'}));

    verifyError(testCase, @() build_room_from_config(ctx), ...
        'BinaScape:Matlab:DuplicateRoomMaterialSlot');
end

function testRejectsAmbiguousShoeboxContractWithExtraSlot(testCase)
    [room, cleanup] = make_distinct_room_cfg(); %#ok<ASGLU>
    ctx = struct('manifest', struct('room', room), ...
        'rpf', MockRavenProject({'matShoebox1', 'matShoebox2', 'matShoebox3', ...
            'matShoebox4', 'matShoebox5', 'matShoebox6', 'otherMaterial'}));

    verifyError(testCase, @() build_room_from_config(ctx), ...
        'BinaScape:Matlab:AmbiguousRoomMaterialSlots');
end

function [room, cleanup] = make_distinct_room_cfg()
    temp_dir = tempname;
    mkdir(temp_dir);
    cleanup = onCleanup(@() rmdir(temp_dir, 's'));

    surface_order = {'north_wall', 'south_wall', 'east_wall', ...
        'west_wall', 'floor', 'ceiling'};
    room = struct();
    room.dimensions_m = [4.2 3.6 2.8];
    room.materials = struct();
    room.material_files = struct();

    for iSurface = 1:numel(surface_order)
        surface = surface_order{iSurface};
        material_id = sprintf('material_%d', iSurface);
        material_path = fullfile(temp_dir, [material_id '.mat']);
        write_material_file(material_path, material_id, 0.1 * iSurface, 0.01 * iSurface);
        room.materials.(surface) = material_id;
        room.material_files.(surface) = struct( ...
            'material_id', material_id, ...
            'material_path', material_path);
    end
end

function write_material_file(material_path, material_name, absorption, scattering)
    absorption_values = strjoin(repmat({sprintf('%.3f', absorption)}, 1, 31), ', ');
    scattering_values = strjoin(repmat({sprintf('%.3f', scattering)}, 1, 31), ', ');
    fid = fopen(material_path, 'w');
    if fid == -1
        error('No se pudo crear material temporal: %s', material_path);
    end
    cleanup = onCleanup(@() fclose(fid)); %#ok<NASGU>
    fprintf(fid, '[Material]\nname=%s\nabsorp=%s\nscatter=%s\n', ...
        material_name, absorption_values, scattering_values);
end
