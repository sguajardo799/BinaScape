function ctx = build_room_from_config(ctx)
    room = ctx.manifest.room;
    rpf = ctx.rpf;

    rpf.setModelToShoebox(room.dimensions_m(1), room.dimensions_m(2), room.dimensions_m(3));

    surface_order = {'ceiling', 'floor', 'north_wall', 'south_wall', 'east_wall', 'west_wall'};
    room_material_names = rpf.getRoomMaterialNames;

    if numel(room_material_names) < numel(surface_order)
        error('RAVEN project does not expose enough room material slots.');
    end

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
        slot_name = room_material_names{iSurface};

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
