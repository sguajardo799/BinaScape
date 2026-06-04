function source_summary = build_static_source_summary(src, source_name, position_m, view_vector, up_vector, start_time_s, original_duration_s)
    source_summary = struct();
    source_summary.source_id = char(string(src.source_id));
    source_summary.source_name = char(string(source_name));
    source_summary.event_type = char(string(getfield_or_default(src, 'event_type', ''))); %#ok<GFLD>
    source_summary.audio_path = char(string(src.audio_path));
    source_summary.directivity_path = char(string(getfield_or_default(src, 'directivity_path', ''))); %#ok<GFLD>
    source_summary.position_m = double(position_m);
    source_summary.view_vector = double(view_vector);
    source_summary.up_vector = double(up_vector);
    source_summary.gain_db = double(getfield_or_default(src, 'gain_db', 0.0)); %#ok<GFLD>
    source_summary.source_start_time_s = double(start_time_s);
    source_summary.source_end_time_s = double(start_time_s + original_duration_s);
    source_summary.source_original_duration_s = double(original_duration_s);
end

function value = getfield_or_default(s, field_name, default_value)
    if isfield(s, field_name) && ~isempty(s.(field_name))
        value = s.(field_name);
    else
        value = default_value;
    end
end
