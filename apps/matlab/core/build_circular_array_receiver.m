function ctx = build_circular_array_receiver(ctx)
    receiver = ctx.manifest.receiver;
    array = validate_circular_array_descriptor(receiver.circular_array, ...
        receiver.position_m, receiver.orientation_deg.yaw);
    n = array.num_microphones;
    positions = vertcat(array.microphones.position_m);
    if isfield(ctx.manifest, 'schema_version') && ...
            any(strcmp(char(string(ctx.manifest.schema_version)), {'2.0', '3.0'}))
        positions = positions .* [1 1 -1];
    end
    names = arrayfun(@(k) sprintf('mic_%04d', k), 0:n-1, 'UniformOutput', false);
    ctx.rpf.setReceiverNames(names);
    ctx.rpf.setReceiverPositions(positions);
    ctx.rpf.setReceiverViewVectors(repmat([1 0 0], n, 1));
    ctx.rpf.setReceiverUpVectors(repmat([0 1 0], n, 1));
    ctx.rpf.setReceiverStates(ones(1, n));
    ctx.rpf.setGenerateRIR(1);
    ctx.rpf.setGenerateBRIR(0);
    ctx.array_receiver_positions = positions;
end
