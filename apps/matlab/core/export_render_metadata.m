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
    if isfield(meta.summary, 'scene_type')
        meta.summary = rmfield(meta.summary, 'scene_type');
    end

    meta.output_type = get_render_output_type(result);
    if isfield(result.summary, 'receiver')
        meta.receiver = result.summary.receiver;
        meta.receiver.coordinate_frame = 'public_xyz_y_up';
        meta.summary = rmfield(meta.summary, 'receiver');
    end
    if isfield(result, 'hrtf_id') && ~isempty(result.hrtf_id)
        meta.hrtf_id = result.hrtf_id;
    end
    if isfield(result, 'hrtf_path') && ~isempty(result.hrtf_path)
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
        meta.batch = struct('n_variants', batch_summary.n_variants);
        if isfield(batch_summary, 'variants')
            other_variants = batch_summary.variants;
            if ~iscell(other_variants)
                other_variants = arrayfun(@(item) item, other_variants, 'UniformOutput', false);
            end
            ids = cellfun(@variant_identity, other_variants, 'UniformOutput', false);
            other_variants = other_variants(~strcmp(ids, meta.output_type));
            if ~isempty(other_variants)
                meta.batch.other_variants = other_variants;
            end
        end
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

function identity = variant_identity(variant)
    if isfield(variant, 'output_type')
        identity = char(string(variant.output_type));
    else
        identity = char(string(variant.hrtf_id));
    end
end
