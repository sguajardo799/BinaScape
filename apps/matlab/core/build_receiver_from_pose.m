function ctx = build_receiver_from_pose(ctx)
    receiver = ctx.manifest.receiver;
    rpf = ctx.rpf;

    if ~isfield(receiver, 'active_hrtf') || isempty(receiver.active_hrtf)
        error('Receiver active_hrtf must be defined before building the RAVEN receiver.');
    end

    hrtf_path = fullfile(dir(receiver.active_hrtf.hrtf_path).folder, dir(receiver.active_hrtf.hrtf_path).name);

    rpf.setReceiverHRTF(string(hrtf_path));
    if is_schema2(ctx.manifest)
        [position, viewVec, ~] = transform_schema2_pose_to_raven( ...
            receiver.position_m, receiver.orientation_deg);
    else
        position = double(receiver.position_m(:).');
        [viewVec, ~] = orientation_deg_to_vectors( ...
            receiver.orientation_deg.pitch, receiver.orientation_deg.yaw);
    end
    rpf.setReceiverPositions(position);
    rpf.setReceiverViewVectors(viewVec);

end

function tf = is_schema2(manifest)
    tf = isfield(manifest, 'schema_version') && strcmp(char(string(manifest.schema_version)), '2.0');
end
