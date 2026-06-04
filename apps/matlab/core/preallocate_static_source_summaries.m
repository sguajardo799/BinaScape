function source_entries = preallocate_static_source_summaries(n_sources)
    source_entries = repmat(empty_static_source_summary_template(), 1, n_sources);
end

function source_summary = empty_static_source_summary_template()
    source_summary = struct();
    source_summary.source_id = '';
    source_summary.source_name = '';
    source_summary.event_type = '';
    source_summary.audio_path = '';
    source_summary.directivity_path = '';
    source_summary.position_m = zeros(1, 3);
    source_summary.view_vector = zeros(1, 3);
    source_summary.up_vector = zeros(1, 3);
    source_summary.gain_db = 0.0;
    source_summary.source_start_time_s = 0.0;
    source_summary.source_end_time_s = 0.0;
    source_summary.source_original_duration_s = 0.0;
end
