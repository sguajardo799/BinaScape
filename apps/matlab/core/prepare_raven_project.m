function ctx = prepare_raven_project(manifest, run_ctx)
    if nargin < 2
        run_ctx = struct();
    end

    base_rpf = manifest.base_rpf_file;
    project_name = manifest.project_name;
    if isfield(run_ctx, 'project_name') && ~isempty(run_ctx.project_name)
        project_name = char(string(run_ctx.project_name));
    end

    output_rpf = fullfile(['C:\ITASoftware\Raven\RavenInput\' project_name '.rpf' ]);

    rpf = itaRavenProject(base_rpf);
    rpf.copyProjectToNewRPFFile(output_rpf);
    rpf.setProjectName(project_name);

    seed_info = struct();
    seed_info.effective_seed = [];
    seed_info.seed_source = 'unspecified';
    seed_info.matlab_rng_applied = false;
    seed_info.raven_seed_applied = false;
    seed_info.note = 'No seed was requested.';

    if isfield(run_ctx, 'effective_seed') && ~isempty(run_ctx.effective_seed)
        rng(double(run_ctx.effective_seed), 'twister'); % TODO: Revisar si fijar seed previo a iterar entre hrtf asegura la misma simulacion
        seed_info.effective_seed = double(run_ctx.effective_seed);
        seed_info.matlab_rng_applied = true;
        seed_info.note = 'Seed applied only to MATLAB rng.';
    end

    if isfield(run_ctx, 'seed_source') && ~isempty(run_ctx.seed_source)
        seed_info.seed_source = char(string(run_ctx.seed_source));
    end

    ctx = struct();
    ctx.rpf = rpf;
    ctx.project_name = project_name;
    ctx.output_rpf = output_rpf;
    ctx.manifest = manifest;
    ctx.run_ctx = run_ctx;
    ctx.seed_info = seed_info;
end
