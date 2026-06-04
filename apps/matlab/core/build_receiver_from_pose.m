function ctx = build_receiver_from_pose(ctx)
    receiver = ctx.manifest.receiver;
    rpf = ctx.rpf;

    if ~isfield(receiver, 'active_hrtf') || isempty(receiver.active_hrtf)
        error('Receiver active_hrtf must be defined before building the RAVEN receiver.');
    end

    hrtf_path = fullfile(dir(receiver.active_hrtf.hrtf_path).folder, dir(receiver.active_hrtf.hrtf_path).name);

    rpf.setReceiverHRTF(string(hrtf_path));
    rpf.setReceiverPositions(receiver.position_m(:).');

    [viewVec, upVec] = orientation_deg_to_vectors(receiver.orientation_deg.pitch, receiver.orientation_deg.yaw);

    rpf.setReceiverViewVectors(viewVec);

end
