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

function testBuildsSchema2ShoeboxFacesWithReflectedZAndInwardWinding(testCase)
    [room, cleanup] = make_schema2_room_cfg('shoebox', ...
        [0 0; 4 0; 4 3; 0 3]); %#ok<ASGLU>
    expected_slots = {'wall_004', 'floor', 'wall_002', ...
        'ceiling', 'wall_001', 'wall_003'};
    ctx = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject(expected_slots));

    ctx = build_room_from_config(ctx);

    verifyEmpty(testCase, ctx.rpf.shoebox_dims);
    verifyEqual(testCase, ctx.rpf.set_faces_calls, 1);
    verifyEqual(testCase, ctx.rpf.face_points(1:4, :), ...
        [0 0 0; 4 0 0; 4 0 -3; 0 0 -3], 'AbsTol', 1e-12);
    verifyEqual(testCase, numel(ctx.rpf.face_definitions), 6);
    verifyEqual(testCase, ctx.rpf.face_materials, ...
        {'wall_001', 'wall_002', 'wall_003', 'wall_004', 'floor', 'ceiling'});

    interior = [2 1.5 -1.5];
    for iFace = 1:numel(ctx.rpf.face_definitions)
        face = ctx.rpf.face_definitions{iFace};
        verifyEqual(testCase, numel(face), 5);
        vertices = ctx.rpf.face_points(face(2:end), :);
        normal = cross(vertices(2, :) - vertices(1, :), vertices(3, :) - vertices(1, :));
        verifyGreaterThan(testCase, dot(normal, interior - mean(vertices, 1)), 0);
    end
end

function testSchema2FacesReferenceTheirOwnMaterialSlotsLikeRealRaven(testCase)
    [room, cleanup] = make_schema2_room_cfg('trapezoid', ...
        [0 0; 5 0; 4 3; 1 3]); %#ok<ASGLU>
    expected_slots = {'wall_001', 'wall_002', 'wall_003', 'wall_004', ...
        'floor', 'ceiling'};
    ctx = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject());

    ctx = build_room_from_config(ctx);

    verifyEqual(testCase, ctx.rpf.room_material_names, expected_slots);
    verifyEqual(testCase, cellfun(@(face) face(1), ctx.rpf.face_definitions), 1:6);
end

function testBuildsLShapeWithTwoRectanglesPerCapAndSharedLogicalMaterials(testCase)
    footprint = [0 0; 5 0; 5 2; 2 2; 2 4; 0 4];
    [room, cleanup] = make_schema2_room_cfg('l_shape', footprint); %#ok<ASGLU>
    expected_slots = {'ceiling_002', 'wall_003', 'floor_001', 'wall_001', ...
        'ceiling_001', 'wall_006', 'floor_002', 'wall_004', 'wall_002', 'wall_005'};
    ctx = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject(expected_slots));

    ctx = build_room_from_config(ctx);

    verifyEqual(testCase, numel(ctx.rpf.face_definitions), 10);
    verifyTrue(testCase, all(cellfun(@(face) numel(face) == 5, ctx.rpf.face_definitions)));
    verifyEqual(testCase, ctx.rpf.face_materials(7:10), ...
        {'floor_001', 'floor_002', 'ceiling_001', 'ceiling_002'});
    floor_applied = ctx.applied_room_materials(strcmp({ctx.applied_room_materials.surface}, 'floor'));
    ceiling_applied = ctx.applied_room_materials(strcmp({ctx.applied_room_materials.surface}, 'ceiling'));
    verifyEqual(testCase, numel(floor_applied), 2);
    verifyEqual(testCase, numel(ceiling_applied), 2);
    verifyEqual(testCase, floor_applied(1).absorp, floor_applied(2).absorp);
    verifyEqual(testCase, ceiling_applied(1).scatter, ceiling_applied(2).scatter);

    floor_area = 0;
    for iFace = 7:8
        face = ctx.rpf.face_definitions{iFace};
        vertices = ctx.rpf.face_points(face(2:end), :);
        floor_area = floor_area + polyarea(vertices(:, 1), vertices(:, 3));
    end
    verifyEqual(testCase, floor_area, 14, 'AbsTol', 1e-12);
end

function testBuildsAllFourLShapeCornersWithOrderedSlotsAndConservedCaps(testCase)
    footprints = { ...
        [0 0; 5 0; 5 2; 2 2; 2 4; 0 4], ... % north_east
        [0 0; 5 0; 5 4; 3 4; 3 2; 0 2], ... % north_west
        [0 0; 2 0; 2 2; 5 2; 5 4; 0 4], ... % south_east
        [0 2; 3 2; 3 0; 5 0; 5 4; 0 4]};    % south_west
    expected_slots = {'wall_001', 'wall_002', 'wall_003', 'wall_004', ...
        'wall_005', 'wall_006', 'floor_001', 'floor_002', ...
        'ceiling_001', 'ceiling_002'};

    for iCorner = 1:numel(footprints)
        [room, cleanup] = make_schema2_room_cfg('l_shape', footprints{iCorner}); %#ok<ASGLU>
        ctx = struct( ...
            'manifest', struct('schema_version', '2.0', 'room', room), ...
            'rpf', MockRavenProject());

        ctx = build_room_from_config(ctx);

        verifyEqual(testCase, ctx.rpf.face_materials, expected_slots);
        verifyEqual(testCase, ctx.rpf.room_material_names, expected_slots);
        verifyEqual(testCase, cellfun(@(face) face(1), ...
            ctx.rpf.face_definitions), 1:10);
        verifyEqual(testCase, {ctx.rpf.material_calls.slot_name}, expected_slots);
        verifyEqual(testCase, cap_area(ctx.rpf, 7:8), 14, 'AbsTol', 1e-12);
        verifyEqual(testCase, cap_area(ctx.rpf, 9:10), 14, 'AbsTol', 1e-12);
        verifyTrue(testCase, all(cellfun(@(face) numel(face) == 5, ...
            ctx.rpf.face_definitions)));
    end
end

function testSchema2RejectsMissingDuplicateAndUnexpectedSlots(testCase)
    [room, cleanup] = make_schema2_room_cfg('trapezoid', ...
        [0 0; 5 0; 4 3; 1 3]); %#ok<ASGLU>
    canonical = {'wall_001', 'wall_002', 'wall_003', 'wall_004', 'floor', 'ceiling'};

    missing = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject(canonical(1:end-1)));
    verifyError(testCase, @() build_room_from_config(missing), ...
        'BinaScape:Matlab:MissingRoomMaterialSlot');

    duplicate = canonical;
    duplicate{end} = duplicate{end - 1};
    duplicated = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject(duplicate));
    verifyError(testCase, @() build_room_from_config(duplicated), ...
        'BinaScape:Matlab:DuplicateRoomMaterialSlot');

    extra = struct('manifest', struct('schema_version', '2.0', 'room', room), ...
        'rpf', MockRavenProject([canonical, {'unexpected'}]));
    verifyError(testCase, @() build_room_from_config(extra), ...
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

function [room, cleanup] = make_schema2_room_cfg(shape_type, footprint)
    temp_dir = tempname;
    mkdir(temp_dir);
    cleanup = onCleanup(@() rmdir(temp_dir, 's'));
    n_walls = size(footprint, 1);
    room = struct();
    room.geometry = struct( ...
        'type', shape_type, ...
        'height_m', 3.0, ...
        'footprint_vertices_m', footprint, ...
        'wall_ids', {arrayfun(@(index) sprintf('wall_%03d', index), ...
            1:n_walls, 'UniformOutput', false)}, ...
        'generated_from', struct());
    room.materials = struct();
    room.material_files = struct();
    surfaces = [room.geometry.wall_ids, {'floor', 'ceiling'}];
    for iSurface = 1:numel(surfaces)
        surface = surfaces{iSurface};
        material_id = sprintf('material_%s', surface);
        material_path = fullfile(temp_dir, [material_id '.mat']);
        write_material_file(material_path, material_id, 0.01 * iSurface, 0.005 * iSurface);
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

function area = cap_area(rpf, face_indices)
    area = 0;
    for iFace = face_indices
        face = rpf.face_definitions{iFace};
        vertices = rpf.face_points(face(2:end), :);
        area = area + polyarea(vertices(:, 1), vertices(:, 3));
    end
end
