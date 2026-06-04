function cfg = validate_static_scene_config(cfg)
    required_top = {'scene_type','scene_id','room','receiver','sources','render', ...
        'base_rpf_file','project_name'};

    for i = 1:numel(required_top)
        if ~isfield(cfg, required_top{i})
            error('Missing required field: %s', required_top{i});
        end
    end

    if ~strcmp(cfg.scene_type, 'static')
        error('Expected scene_type = static');
    end

    cfg.room = normalize_room_config(cfg.room);

    if ~isfield(cfg.receiver, 'position_m') || ~isfield(cfg.receiver, 'orientation_deg')
        error('Static receiver must define position_m and orientation_deg');
    end

    cfg.receiver.position_m = double(cfg.receiver.position_m(:)).';
    cfg.receiver.orientation_deg = normalize_orientation(cfg.receiver.orientation_deg);
    cfg.sources = normalize_static_sources(cfg.sources);
    cfg.background_noise = normalize_background_noise_config(getfield_if_present(cfg, 'background_noise')); %#ok<GFLD>

    if ~isfield(cfg.render, 'sample_rate_hz') || ~isfield(cfg.render, 'output_wav_path') ...
            || ~isfield(cfg.render, 'output_metadata_path')
        error('Render section incomplete');
    end

    cfg.render.sample_rate_hz = double(cfg.render.sample_rate_hz);
    cfg.render.output_wav_path = char(string(cfg.render.output_wav_path));
    cfg.render.output_metadata_path = char(string(cfg.render.output_metadata_path));
    cfg.render.trim_reverb_tail = normalize_optional_logical_field( ...
        getfield_if_present(cfg.render, 'trim_reverb_tail'), false, 'render.trim_reverb_tail'); %#ok<GFLD>
    if isfield(cfg.render, 'target_duration_s') && ~isempty(cfg.render.target_duration_s)
        cfg.render.target_duration_s = normalize_target_duration(cfg.render.target_duration_s);
    end
    cfg.project_name = char(string(cfg.project_name));
    cfg.base_rpf_file = char(string(cfg.base_rpf_file));

    [effective_seed, seed_source] = resolve_effective_seed(cfg);
    cfg.render.effective_seed = effective_seed;
    cfg.render.seed_source = seed_source;
    if ~isempty(effective_seed)
        cfg.render.seed = effective_seed;
    end

    cfg.receiver.hrtfs = normalize_receiver_hrtfs(cfg.receiver);
    cfg.render.outputs = derive_render_outputs(cfg.render, cfg.project_name, cfg.scene_id, cfg.receiver.hrtfs);
end

function background_noise = normalize_background_noise_config(background_noise)
    if isempty(background_noise)
        background_noise = struct('enabled', false, 'layers', struct([]));
        return;
    end

    if ~isstruct(background_noise) || ~isscalar(background_noise)
        error('background_noise must be an object when present.');
    end

    background_noise.enabled = normalize_optional_logical_field( ...
        getfield_if_present(background_noise, 'enabled'), false, 'background_noise.enabled'); %#ok<GFLD>

    if ~background_noise.enabled
        if ~isfield(background_noise, 'layers') || isempty(background_noise.layers)
            background_noise.layers = struct([]);
        else
            background_noise.layers = normalize_background_noise_layers(background_noise.layers, false);
        end
        return;
    end

    if ~isfield(background_noise, 'layers') || isempty(background_noise.layers)
        error('background_noise.layers must be a non-empty array when background_noise.enabled is true.');
    end

    background_noise.layers = normalize_background_noise_layers(background_noise.layers, true);
end

function layers = normalize_background_noise_layers(layers, require_valid)
    if iscell(layers)
        layers = normalize_struct_cell_array(layers, 'background_noise.layers');
    end

    if isempty(layers)
        layers = struct([]);
        return;
    end

    if ~isstruct(layers)
        error('background_noise.layers must be an object array.');
    end

    raw_layers = layers(:).';
    normalized_cells = cell(1, numel(raw_layers));
    for i = 1:numel(raw_layers)
        layer_path = sprintf('background_noise.layers(%d)', i);
        if require_valid
            normalized_cells{i} = normalize_background_noise_layer(raw_layers(i), layer_path);
        else
            normalized_cells{i} = raw_layers(i);
        end
    end
    layers = normalize_struct_cell_array(normalized_cells, 'background_noise.layers');
end

function layer = normalize_background_noise_layer(layer, layer_path)
    if ~isfield(layer, 'strategy') || isempty(layer.strategy)
        error('%s.strategy is required.', layer_path);
    end

    original_strategy = char(string(layer.strategy));
    strategy = lower(strtrim(original_strategy));
    if strcmp(strategy, 'audio_folder')
        strategy = 'audio_file';
    elseif ~any(strcmp(strategy, {'colored', 'audio_file'}))
        error('%s.strategy must be colored or audio_file.', layer_path);
    end

    layer.strategy_original = original_strategy;
    layer.strategy = strategy;

    if ~isfield(layer, 'snr_db') || isempty(layer.snr_db) || ...
            ~isnumeric(layer.snr_db) || ~isscalar(layer.snr_db) || ~isfinite(layer.snr_db)
        error('%s.snr_db must be a finite numeric scalar.', layer_path);
    end
    layer.snr_db = double(layer.snr_db);

    if isfield(layer, 'seed') && ~isempty(layer.seed)
        if ~isnumeric(layer.seed) || ~isscalar(layer.seed) || ~isfinite(layer.seed) || round(double(layer.seed)) ~= double(layer.seed)
            error('%s.seed must be a finite integer scalar when present.', layer_path);
        end
        layer.seed = double(layer.seed);
    end

    if strcmp(strategy, 'colored')
        if ~isfield(layer, 'color') || isempty(layer.color)
            error('%s.color is required for colored background noise.', layer_path);
        end
        color = lower(strtrim(char(string(layer.color))));
        if ~any(strcmp(color, {'white', 'pink', 'brown', 'blue', 'violet'}))
            error('%s.color must be one of: white, pink, brown, blue, violet.', layer_path);
        end
        layer.color = color;
    else
        if ~isfield(layer, 'path') || isempty(layer.path)
            error('%s.path is required for audio_file background noise.', layer_path);
        end
        layer.path = char(string(layer.path));
        if isfolder(layer.path)
            error('%s.path must point to a WAV file, not a folder.', layer_path);
        end
        if ~isfile(layer.path)
            error('%s.path does not exist: %s', layer_path, layer.path);
        end
        [~, ~, ext] = fileparts(layer.path);
        if ~strcmpi(ext, '.wav')
            error('%s.path must point to a .wav file.', layer_path);
        end
    end
end

function values = normalize_struct_cell_array(value_cells, field_name)
    if isempty(value_cells)
        values = struct([]);
        return;
    end

    for i = 1:numel(value_cells)
        if ~isstruct(value_cells{i}) || ~isscalar(value_cells{i})
            error('%s must be an object array.', field_name);
        end
    end

    field_names = fieldnames(value_cells{1});
    for i = 2:numel(value_cells)
        field_names = union(field_names, fieldnames(value_cells{i}), 'stable');
    end

    normalized_cells = cell(size(value_cells));
    for i = 1:numel(value_cells)
        item = cell2struct(repmat({[]}, 1, numel(field_names)), field_names, 2);
        item_fields = fieldnames(value_cells{i});
        for j = 1:numel(item_fields)
            item.(item_fields{j}) = value_cells{i}.(item_fields{j});
        end
        normalized_cells{i} = item;
    end

    values = [normalized_cells{:}];
end

function orientation = normalize_orientation(orientation)
    required = {'yaw','pitch','roll'};
    for i = 1:numel(required)
        field_name = required{i};
        if ~isfield(orientation, field_name) || isempty(orientation.(field_name))
            orientation.(field_name) = 0.0;
        else
            orientation.(field_name) = double(orientation.(field_name));
        end
    end
end

function target_duration_s = normalize_target_duration(target_duration_s)
    if ~isscalar(target_duration_s) || ~isnumeric(target_duration_s) || ~isfinite(target_duration_s)
        error('render.target_duration_s must be a finite numeric scalar.');
    end

    target_duration_s = double(target_duration_s);
    if target_duration_s <= 0
        error('render.target_duration_s must be greater than zero.');
    end
end

function sources = normalize_static_sources(sources)
    if iscell(sources)
        sources = normalize_source_cell_array(sources);
    end

    if ~isstruct(sources) || isempty(sources)
        error('sources must be a non-empty object or object array.');
    end

    sources = sources(:).';

    for i = 1:numel(sources)
        if isfield(sources(i), 'source_id') && ~isempty(sources(i).source_id)
            sources(i).source_id = char(string(sources(i).source_id));
        end

        if isfield(sources(i), 'position_m') && ~isempty(sources(i).position_m)
            sources(i).position_m = double(sources(i).position_m(:)).';
        end

        if isfield(sources(i), 'orientation_deg') && ~isempty(sources(i).orientation_deg)
            sources(i).orientation_deg = normalize_orientation(sources(i).orientation_deg);
        end

        if isfield(sources(i), 'event_type') && ~isempty(sources(i).event_type)
            sources(i).event_type = char(string(sources(i).event_type));
        end

        if isfield(sources(i), 'audio_path') && ~isempty(sources(i).audio_path)
            sources(i).audio_path = char(string(sources(i).audio_path));
        end

        if isfield(sources(i), 'directivity_path') && ~isempty(sources(i).directivity_path)
            sources(i).directivity_path = char(string(sources(i).directivity_path));
        end

        if isfield(sources(i), 'gain_db') && ~isempty(sources(i).gain_db)
            sources(i).gain_db = double(sources(i).gain_db);
        end

        if isfield(sources(i), 'start_time_s') && ~isempty(sources(i).start_time_s)
            sources(i).start_time_s = double(sources(i).start_time_s);
        end
    end
end

function sources = normalize_source_cell_array(source_cells)
    if isempty(source_cells)
        sources = struct([]);
        return;
    end

    for i = 1:numel(source_cells)
        if ~isstruct(source_cells{i}) || ~isscalar(source_cells{i})
            error('sources must be a non-empty object or object array.');
        end
    end

    field_names = fieldnames(source_cells{1});
    for i = 2:numel(source_cells)
        field_names = union(field_names, fieldnames(source_cells{i}), 'stable');
    end

    normalized_cells = cell(size(source_cells));
    for i = 1:numel(source_cells)
        normalized_source = cell2struct(repmat({[]}, 1, numel(field_names)), field_names, 2);
        source_field_names = fieldnames(source_cells{i});
        for j = 1:numel(source_field_names)
            field_name = source_field_names{j};
            normalized_source.(field_name) = source_cells{i}.(field_name);
        end
        normalized_cells{i} = normalized_source;
    end

    sources = [normalized_cells{:}];
end

function value = normalize_optional_logical_field(value, default_value, field_name)
    if nargin < 2
        default_value = false;
    end

    if nargin < 3
        field_name = 'value';
    end

    if isempty(value)
        value = logical(default_value);
        return;
    end

    if islogical(value) && isscalar(value)
        return;
    end

    if isnumeric(value) && isscalar(value) && isfinite(value) && any(value == [0 1])
        value = logical(value);
        return;
    end

    error('%s must be a logical scalar.', field_name);
end

function room = normalize_room_config(room)
    if ~isfield(room, 'dimensions_m') || numel(room.dimensions_m) ~= 3
        error('room.dimensions_m must define exactly three values.');
    end

    room.dimensions_m = double(room.dimensions_m(:)).';

    required_surfaces = get_required_room_surfaces();

    if ~isfield(room, 'materials') || ~isstruct(room.materials)
        error('room.materials is required for the static flow.');
    end

    if ~isfield(room, 'material_files') || ~isstruct(room.material_files)
        error('room.material_files is required for the static flow.');
    end

    normalized_materials = struct();
    normalized_material_files = struct();

    for i = 1:numel(required_surfaces)
        surface = required_surfaces{i};

        if ~isfield(room.materials, surface) || isempty(room.materials.(surface))
            error('room.materials.%s is required.', surface);
        end
        if ~isfield(room.material_files, surface) || ~isstruct(room.material_files.(surface))
            error('room.material_files.%s is required.', surface);
        end

        surface_material = char(string(room.materials.(surface)));
        material_ref = room.material_files.(surface);

        if ~isfield(material_ref, 'material_id') || isempty(material_ref.material_id)
            error('room.material_files.%s.material_id is required.', surface);
        end
        if ~isfield(material_ref, 'material_path') || isempty(material_ref.material_path)
            error('room.material_files.%s.material_path is required.', surface);
        end

        material_id = char(string(material_ref.material_id));
        material_path = char(string(material_ref.material_path));

        if ~strcmp(surface_material, material_id)
            error(['room.materials.%s (%s) must match room.material_files.%s.material_id (%s).'], ...
                surface, surface_material, surface, material_id);
        end

        if ~is_absolute_path(material_path)
            error('room.material_files.%s.material_path must be an absolute path: %s', surface, material_path);
        end

        if ~isfile(material_path)
            error('room.material_files.%s.material_path does not exist: %s', surface, material_path);
        end

        normalized_materials.(surface) = surface_material;
        normalized_material_files.(surface) = struct( ...
            'material_id', material_id, ...
            'material_path', material_path);
    end

    room.materials = normalized_materials;
    room.material_files = normalized_material_files;
end

function tf = is_absolute_path(path_value)
    path_value = char(string(path_value));
    tf = ~isempty(regexp(path_value, '^[A-Za-z]:[\\/]', 'once')) || ...
        startsWith(path_value, '\\') || startsWith(path_value, '/');
end

function surfaces = get_required_room_surfaces()
    surfaces = {'north_wall', 'south_wall', 'east_wall', 'west_wall', 'floor', 'ceiling'};
end

function [effective_seed, seed_source] = resolve_effective_seed(cfg)
    has_render_seed = isfield(cfg.render, 'seed') && ~isempty(cfg.render.seed);
    has_legacy_seed = isfield(cfg, 'seed') && ~isempty(cfg.seed);

    if has_render_seed
        effective_seed = double(cfg.render.seed);
        seed_source = 'render.seed';
        return;
    end

    if has_legacy_seed
        effective_seed = double(cfg.seed);
        seed_source = 'seed';
        return;
    end

    effective_seed = [];
    seed_source = 'unspecified';
end

function hrtfs = normalize_receiver_hrtfs(receiver)
    has_singular = isfield(receiver, 'hrtf') && ~isempty(receiver.hrtf);
    has_plural = isfield(receiver, 'hrtfs') && ~isempty(receiver.hrtfs);

    if ~has_singular && ~has_plural
        error('Static receiver must define receiver.hrtf or receiver.hrtfs.');
    end

    singular_items = normalize_hrtf_container(getfield_if_present(receiver, 'hrtf')); %#ok<GFLD>
    plural_items = normalize_hrtf_container(getfield_if_present(receiver, 'hrtfs')); %#ok<GFLD>

    if has_singular && has_plural && ~hrtf_lists_match(singular_items, plural_items)
        error('receiver.hrtf and receiver.hrtfs cannot declare different HRTF content simultaneously.');
    end

    if has_plural
        raw_items = plural_items;
    else
        raw_items = singular_items;
    end

    if isempty(raw_items)
        error('receiver.hrtfs must contain at least one HRTF entry.');
    end

    hrtfs = repmat(struct('hrtf_id', '', 'hrtf_path', '', 'original_hrtf_id', ''), 1, numel(raw_items));
    used_ids = containers.Map('KeyType', 'char', 'ValueType', 'double');

    for i = 1:numel(raw_items)
        item = raw_items(i);

        if ~isfield(item, 'hrtf_id') || isempty(item.hrtf_id)
            error('receiver.hrtfs(%d) must define hrtf_id.', i);
        end
        if ~isfield(item, 'hrtf_path') || isempty(item.hrtf_path)
            error('receiver.hrtfs(%d) must define hrtf_path.', i);
        end

        original_id = char(string(item.hrtf_id));
        safe_id = sanitize_hrtf_id(original_id, char(string(item.hrtf_path)));
        unique_id = uniquify_hrtf_id(safe_id, used_ids);

        hrtfs(i).hrtf_id = unique_id;
        hrtfs(i).original_hrtf_id = original_id;
        hrtfs(i).hrtf_path = char(string(item.hrtf_path));
    end
end

function items = normalize_hrtf_container(value)
    if isempty(value)
        items = struct([]);
        return;
    end

    if iscell(value)
        value = [value{:}];
    end

    if ~isstruct(value)
        error('receiver.hrtf(s) must be an object or object array.');
    end

    items = value(:).';
end

function matches = hrtf_lists_match(lhs, rhs)
    if numel(lhs) ~= numel(rhs)
        matches = false;
        return;
    end

    matches = true;
    for i = 1:numel(lhs)
        if ~strcmp(char(string(lhs(i).hrtf_id)), char(string(rhs(i).hrtf_id))) || ...
                ~strcmp(char(string(lhs(i).hrtf_path)), char(string(rhs(i).hrtf_path)))
            matches = false;
            return;
        end
    end
end

function value = getfield_if_present(s, field_name)
    if isfield(s, field_name)
        value = s.(field_name);
    else
        value = [];
    end
end

function safe_id = sanitize_hrtf_id(raw_id, fallback_path)
    safe_id = lower(strtrim(raw_id));
    safe_id = regexprep(safe_id, '[^a-zA-Z0-9_-]+', '-');
    safe_id = regexprep(safe_id, '-+', '-');
    safe_id = regexprep(safe_id, '(^[-_]+|[-_]+$)', '');

    if isempty(safe_id)
        [~, fallback_name] = fileparts(fallback_path);
        safe_id = lower(regexprep(fallback_name, '[^a-zA-Z0-9_-]+', '-'));
        safe_id = regexprep(safe_id, '-+', '-');
        safe_id = regexprep(safe_id, '(^[-_]+|[-_]+$)', '');
    end

    if isempty(safe_id)
        error('Could not derive a safe hrtf_id from path: %s', fallback_path);
    end
end

function unique_id = uniquify_hrtf_id(base_id, used_ids)
    if ~isKey(used_ids, base_id)
        used_ids(base_id) = 1;
        unique_id = base_id;
        return;
    end

    next_index = used_ids(base_id) + 1;
    used_ids(base_id) = next_index;
    unique_id = sprintf('%s-%d', base_id, next_index);
end

function outputs = derive_render_outputs(render_cfg, project_name, scene_id, hrtfs)
    n_hrtfs = numel(hrtfs);
    outputs = repmat(struct('hrtf_id', '', 'wav_path', '', 'metadata_path', '', ...
        'project_name', ''), 1, n_hrtfs);

    base_wav_path = resolve_output_target(render_cfg.output_wav_path, scene_id, '.wav');
    base_metadata_path = resolve_output_target(render_cfg.output_metadata_path, scene_id, '.json');

    for i = 1:n_hrtfs
        hrtf_id = hrtfs(i).hrtf_id;

        outputs(i).hrtf_id = hrtf_id;
        if n_hrtfs == 1
            outputs(i).wav_path = base_wav_path;
            outputs(i).metadata_path = base_metadata_path;
            outputs(i).project_name = project_name;
        else
            outputs(i).wav_path = append_suffix_to_path(base_wav_path, hrtf_id);
            outputs(i).metadata_path = append_suffix_to_path(base_metadata_path, hrtf_id);
            outputs(i).project_name = sprintf('%s__%s', project_name, hrtf_id);
        end
    end
end

function output_path = resolve_output_target(input_path, scene_id, default_ext)
    input_path = char(string(input_path));

    if is_directory_output(input_path)
        output_path = fullfile(input_path, sprintf('%s%s', scene_id, default_ext));
    else
        output_path = input_path;
    end
end

function tf = is_directory_output(input_path)
    input_path = char(string(input_path));
    tf = endsWith(input_path, '/') || endsWith(input_path, '\\') || isfolder(input_path);
end

function output_path = append_suffix_to_path(input_path, suffix)
    [folder, base_name, ext] = fileparts(char(string(input_path)));
    output_path = fullfile(folder, sprintf('%s__%s%s', base_name, suffix, ext));
end
