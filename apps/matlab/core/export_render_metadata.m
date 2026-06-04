function export_render_metadata(cfg, result, output_metadata_path, batch_summary)
    if nargin < 4
        batch_summary = [];
    end

    meta = struct();
    meta.schema_version = cfg.schema_version;
    meta.scene_id = cfg.scene_id;
    meta.job_id = cfg.job_id;
    meta.scene_type = cfg.scene_type;
    meta.sample_rate_hz = result.fs;
    meta.summary = result.summary;

    if isfield(result, 'hrtf_id')
        meta.hrtf_id = result.hrtf_id;
    end
    if isfield(result, 'hrtf_path')
        meta.hrtf_path = result.hrtf_path;
    end
    if isfield(cfg, 'render') && isfield(cfg.render, 'effective_seed')
        meta.effective_seed = cfg.render.effective_seed;
        meta.seed_source = cfg.render.seed_source;
    end
    if isfield(result, 'output')
        meta.output_wav_path = result.output.wav_path;
        meta.output_metadata_path = result.output.metadata_path;
        meta.project_name = result.output.project_name;
    else
        meta.output_metadata_path = output_metadata_path;
    end
    if ~isempty(batch_summary)
        meta.batch = batch_summary;
    elseif isfield(result, 'summary') && isfield(result.summary, 'batch')
        meta.batch = result.summary.batch;
    end

    txt = jsonencode(meta, 'PrettyPrint', true);

    out_dir = fileparts(output_metadata_path);
    if ~isempty(out_dir) && ~isfolder(out_dir)
        mkdir(out_dir);
    end

    fid = fopen(output_metadata_path, 'w');
    if fid == -1
        error('Could not open metadata output file: %s', output_metadata_path);
    end

    fwrite(fid, txt, 'char');
    fclose(fid);
end
