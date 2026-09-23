function ctx = build_room_from_config(ctx)
    if isfield(ctx.manifest, 'schema_version') && strcmp(char(string(ctx.manifest.schema_version)), '2.0')
        ctx = build_polygon_room_from_config(ctx);
        return;
    end

    ctx = build_legacy_room_from_config(ctx);
end

function ctx = build_polygon_room_from_config(ctx)
    room = ctx.manifest.room;
    rpf = ctx.rpf;
    geometry = room.geometry;
    footprint = double(geometry.footprint_vertices_m);
    n_walls = size(footprint, 1);
    height_m = double(geometry.height_m);
    wall_ids = geometry.wall_ids;
    if isstring(wall_ids)
        wall_ids = cellstr(wall_ids(:).');
    end

    bottom_points = [footprint(:, 1), zeros(n_walls, 1), -footprint(:, 2)];
    top_points = [footprint(:, 1), height_m * ones(n_walls, 1), -footprint(:, 2)];
    points = [bottom_points; top_points];
    faces = {};
    face_materials = {};

    % A reflected [x,z] footprint reverses handedness. Orient each wall using
    % a local point just inside its public CCW edge, transformed to RAVEN.
    for iWall = 1:n_walls
        iNext = mod(iWall, n_walls) + 1;
        edge = footprint(iNext, :) - footprint(iWall, :);
        inward = [-edge(2), edge(1)] / norm(edge);
        midpoint = (footprint(iWall, :) + footprint(iNext, :)) / 2;
        local_interior = midpoint + 1e-6 * inward;
        raven_interior = [local_interior(1), height_m / 2, -local_interior(2)];
        indices = [iWall, iNext, n_walls + iNext, n_walls + iWall];
        indices = orient_face_toward(points, indices, raven_interior);
        faces{end + 1} = [1, indices]; %#ok<AGROW>
        face_materials{end + 1} = wall_ids{iWall}; %#ok<AGROW>
    end

    if strcmp(geometry.type, 'l_shape')
        cap_rectangles = decompose_l_shape_into_rectangles(footprint);
        floor_slots = {'floor_001', 'floor_002'};
        ceiling_slots = {'ceiling_001', 'ceiling_002'};
    else
        cap_rectangles = {footprint};
        floor_slots = {'floor'};
        ceiling_slots = {'ceiling'};
    end

    interior_xz = cap_interior_reference(cap_rectangles);
    raven_interior = [interior_xz(1), height_m / 2, -interior_xz(2)];
    floor_faces = cell(1, numel(cap_rectangles));
    ceiling_faces = cell(1, numel(cap_rectangles));
    for iCap = 1:numel(cap_rectangles)
        if numel(cap_rectangles) == 1
            floor_indices = 1:n_walls;
            ceiling_indices = n_walls + (1:n_walls);
        else
            rectangle = cap_rectangles{iCap};
            first_index = size(points, 1) + 1;
            points = [points; ...
                rectangle(:, 1), zeros(4, 1), -rectangle(:, 2); ...
                rectangle(:, 1), height_m * ones(4, 1), -rectangle(:, 2)]; %#ok<AGROW>
            floor_indices = first_index:(first_index + 3);
            ceiling_indices = (first_index + 4):(first_index + 7);
        end
        floor_indices = orient_face_toward(points, floor_indices, raven_interior);
        ceiling_indices = orient_face_toward(points, ceiling_indices, raven_interior);
        floor_faces{iCap} = [1, floor_indices];
        ceiling_faces{iCap} = [1, ceiling_indices];
    end

    faces = [faces, floor_faces, ceiling_faces];
    face_materials = [face_materials, floor_slots, ceiling_slots];
    expected = face_materials;
    % itaRavenProject interprets the first face element as a one-based index
    % into the materials array. Each technical face has its own stable slot,
    % including the two floor/ceiling rectangles used for L-shaped caps.
    for iFace = 1:numel(faces)
        faces{iFace}(1) = iFace;
    end
    rpf.setModelToFaces(points, faces, face_materials);
    actual = normalize_polygon_slots(rpf.getRoomMaterialNames);
    if numel(unique(actual)) ~= numel(actual), error('BinaScape:Matlab:DuplicateRoomMaterialSlot','RAVEN devolvió slots duplicados.'); end
    missing = expected(~ismember(expected,actual)); if ~isempty(missing), error('BinaScape:Matlab:MissingRoomMaterialSlot','Faltan slots: %s.',strjoin(missing,', ')); end
    if numel(actual) ~= numel(expected) || any(~ismember(actual,expected)), error('BinaScape:Matlab:AmbiguousRoomMaterialSlots','Contrato de slots ambiguo.'); end
    for i=1:n_walls, apply_polygon_material(rpf,room,wall_ids{i},wall_ids{i}); end
    for i=1:numel(floor_slots), apply_polygon_material(rpf,room,'floor',floor_slots{i}); end
    for i=1:numel(ceiling_slots), apply_polygon_material(rpf,room,'ceiling',ceiling_slots{i}); end
    logical=[wall_ids,repmat({'floor'},1,numel(floor_slots)),repmat({'ceiling'},1,numel(ceiling_slots))]; applied=repmat(struct('surface','','slot_name','','material_id','','material_path','','absorp',[],'scatter',[]),1,numel(logical));
    for i=1:numel(logical), ref=room.material_files.(logical{i}); mat=parse_room_material_file(ref.material_path); applied(i)=struct('surface',logical{i},'slot_name',expected{i},'material_id',ref.material_id,'material_path',ref.material_path,'absorp',mat.absorp,'scatter',mat.scatter); end
    ctx.rpf=rpf; ctx.applied_room_materials=applied;
end

function names = normalize_polygon_slots(value)
    if isstring(value), names=cellstr(value(:).'); elseif iscell(value), names=reshape(value,1,[]); else, error('BinaScape:Matlab:AmbiguousRoomMaterialSlots','Slots inválidos.'); end
    names=cellfun(@(x)char(string(x)),names,'UniformOutput',false);
end
function apply_polygon_material(rpf,room,surface,slot)
    ref=room.material_files.(surface); mat=parse_room_material_file(ref.material_path); rpf.setMaterial(slot,mat.absorp,mat.scatter);
end

function indices = orient_face_toward(points, indices, interior)
    vertices = points(indices, :);
    score = dot(cross(vertices(2, :) - vertices(1, :), ...
        vertices(3, :) - vertices(1, :)), interior - mean(vertices, 1));
    if score <= 0
        indices = fliplr(indices);
    end
end

function rectangles = decompose_l_shape_into_rectangles(vertices)
    x_values = unique(vertices(:, 1));
    z_values = unique(vertices(:, 2));
    if numel(x_values) ~= 3 || numel(z_values) ~= 3
        error('BinaScape:Matlab:InvalidLShape', ...
            'Una huella L debe definir exactamente tres coordenadas x y z.');
    end

    occupied = false(2, 2);
    for iX = 1:2
        for iZ = 1:2
            center_x = mean(x_values(iX:iX + 1));
            center_z = mean(z_values(iZ:iZ + 1));
            occupied(iX, iZ) = inpolygon(center_x, center_z, ...
                vertices(:, 1), vertices(:, 2));
        end
    end
    [missing_x, missing_z] = find(~occupied);
    if numel(missing_x) ~= 1 || nnz(occupied) ~= 3
        error('BinaScape:Matlab:InvalidLShape', ...
            'La huella L debe equivaler a un rectángulo exterior menos una esquina.');
    end

    kept_x = 3 - missing_x;
    kept_z = 3 - missing_z;
    full_strip = rectangle_vertices( ...
        x_values(kept_x), x_values(kept_x + 1), z_values(1), z_values(3));
    side_strip = rectangle_vertices( ...
        x_values(missing_x), x_values(missing_x + 1), ...
        z_values(kept_z), z_values(kept_z + 1));
    rectangles = {full_strip, side_strip};

    rectangle_area = poly_area2(full_strip) + poly_area2(side_strip);
    if abs(rectangle_area - poly_area2(vertices)) > 1e-8
        error('BinaScape:Matlab:InvalidLShape', ...
            'La descomposición rectangular de la huella L no conserva el área.');
    end
end

function vertices = rectangle_vertices(x_min, x_max, z_min, z_max)
    vertices = [x_min z_min; x_max z_min; x_max z_max; x_min z_max];
end

function point = cap_interior_reference(rectangles)
    if numel(rectangles) == 1
        point = polygon_centroid(rectangles{1});
        return;
    end
    areas = cellfun(@poly_area2, rectangles);
    [~, largest_index] = max(areas);
    point = mean(rectangles{largest_index}, 1);
end

function point = polygon_centroid(vertices)
    next = circshift(vertices, -1, 1);
    cross_terms = vertices(:, 1) .* next(:, 2) - next(:, 1) .* vertices(:, 2);
    signed_area = 0.5 * sum(cross_terms);
    point = [ ...
        sum((vertices(:, 1) + next(:, 1)) .* cross_terms), ...
        sum((vertices(:, 2) + next(:, 2)) .* cross_terms)] / (6 * signed_area);
end

function area=poly_area2(vertices)
    next=circshift(vertices,-1,1); area=abs(0.5*sum(vertices(:,1).*next(:,2)-next(:,1).*vertices(:,2)));
end

function ctx = build_legacy_room_from_config(ctx)
    room = ctx.manifest.room;
    rpf = ctx.rpf;

    rpf.setModelToShoebox(room.dimensions_m(1), room.dimensions_m(2), room.dimensions_m(3));

    slot_names = {'matShoebox1', 'matShoebox2', 'matShoebox3', ...
        'matShoebox4', 'matShoebox5', 'matShoebox6'};
    surface_order = {'floor', 'ceiling', 'south_wall', ...
        'west_wall', 'north_wall', 'east_wall'};
    room_material_names = normalize_room_material_names(rpf.getRoomMaterialNames);
    validate_shoebox_material_slots(room_material_names, slot_names);

    applied_room_materials = repmat(struct( ...
        'surface', '', ...
        'slot_name', '', ...
        'material_id', '', ...
        'material_path', '', ...
        'absorp', [], ...
        'scatter', []), 1, numel(surface_order));

    for iSurface = 1:numel(surface_order)
        surface = surface_order{iSurface};
        material_ref = room.material_files.(surface);
        material = parse_room_material_file(material_ref.material_path);
        slot_name = slot_names{iSurface};

        rpf.setMaterial(slot_name, material.absorp, material.scatter);

        applied_room_materials(iSurface).surface = surface;
        applied_room_materials(iSurface).slot_name = slot_name;
        applied_room_materials(iSurface).material_id = material_ref.material_id;
        applied_room_materials(iSurface).material_path = material_ref.material_path;
        applied_room_materials(iSurface).absorp = material.absorp;
        applied_room_materials(iSurface).scatter = material.scatter;
    end

    ctx.rpf = rpf;
    ctx.applied_room_materials = applied_room_materials;
end

function names = normalize_room_material_names(candidate)
    if isstring(candidate)
        if any(ismissing(candidate), 'all')
            error('BinaScape:Matlab:AmbiguousRoomMaterialSlots', ...
                'RAVEN devolvió nombres de slots vacíos o no definidos.');
        end
        names = cellstr(candidate(:));
    elseif iscell(candidate)
        names = reshape(candidate, 1, []);
        for iName = 1:numel(names)
            if ~(ischar(names{iName}) || (isstring(names{iName}) && isscalar(names{iName})))
                error('BinaScape:Matlab:AmbiguousRoomMaterialSlots', ...
                    'RAVEN devolvió un nombre de slot no textual en la posición %d.', iName);
            end
            names{iName} = char(string(names{iName}));
        end
    else
        error('BinaScape:Matlab:AmbiguousRoomMaterialSlots', ...
            'getRoomMaterialNames debe devolver una lista de nombres de slots.');
    end

    if any(cellfun(@isempty, names))
        error('BinaScape:Matlab:AmbiguousRoomMaterialSlots', ...
            'RAVEN devolvió uno o más nombres de slots vacíos.');
    end
end

function validate_shoebox_material_slots(actual_names, expected_names)
    unique_actual_names = unique(actual_names);
    if numel(unique_actual_names) ~= numel(actual_names)
        duplicate_names = actual_names;
        duplicate_names = duplicate_names(arrayfun(@(i) ...
            sum(strcmp(actual_names{i}, actual_names)) > 1, 1:numel(actual_names)));
        error('BinaScape:Matlab:DuplicateRoomMaterialSlot', ...
            'RAVEN devolvió slots de material duplicados: %s.', ...
            strjoin(unique(duplicate_names), ', '));
    end

    missing_names = expected_names(~ismember(expected_names, actual_names));
    if ~isempty(missing_names)
        error('BinaScape:Matlab:MissingRoomMaterialSlot', ...
            'Faltan slots canónicos de shoebox RAVEN: %s. Recibidos: %s.', ...
            strjoin(missing_names, ', '), strjoin(actual_names, ', '));
    end

    unexpected_names = actual_names(~ismember(actual_names, expected_names));
    if numel(actual_names) ~= numel(expected_names) || ~isempty(unexpected_names)
        error('BinaScape:Matlab:AmbiguousRoomMaterialSlots', ...
            'Contrato de slots shoebox ambiguo. Esperados: %s. Recibidos: %s.', ...
            strjoin(expected_names, ', '), strjoin(actual_names, ', '));
    end
end
