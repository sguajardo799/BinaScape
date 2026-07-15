function ctx = build_room_from_config(ctx)
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
