function tests = test_build_static_source_summary
    tests = functiontests(localfunctions);
end

function testBuildsPerSourceTimingWithoutRoomReverberation(testCase)
    src = struct( ...
        'source_id', 'src_01', ...
        'event_type', 'speech', ...
        'audio_path', 'C:\audio\event.wav', ...
        'directivity_path', 'C:\directivity\voice.daff', ...
        'gain_db', -3.0);

    summary = build_static_source_summary( ...
        src, 'src_01', [1.0 2.0 3.0], [0.0 1.0 0.0], [0.0 0.0 1.0], 1.25, 0.75);

    verifyEqual(testCase, summary.source_id, 'src_01');
    verifyEqual(testCase, summary.source_start_time_s, 1.25);
    verifyEqual(testCase, summary.source_end_time_s, 2.0, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.source_original_duration_s, 0.75);
    verifyEqual(testCase, summary.directivity_path, 'C:\directivity\voice.daff');
    verifyFalse(testCase, isfield(summary, 'reverberation'));
end

function testBuildsPerSourceSummaryWithoutOptionalFields(testCase)
    src = struct('source_id', 'src_02', 'audio_path', 'C:\audio\event.wav');

    summary = build_static_source_summary( ...
        src, 'src_02', [0.0 0.0 0.0], [1.0 0.0 0.0], [0.0 0.0 1.0], 0.5, 1.25);

    verifyEqual(testCase, summary.source_end_time_s, 1.75, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.directivity_path, '');
    verifyFalse(testCase, isfield(summary, 'reverberation'));
end

function testPreallocatedSourceSummariesAcceptBuiltEntries(testCase)
    entries = preallocate_static_source_summaries(2);
    src = struct('source_id', 'src', 'audio_path', 'C:\audio\event.wav');

    entries(1) = build_static_source_summary( ...
        src, 'src_a', [1.0 0.0 0.0], [1.0 0.0 0.0], [0.0 0.0 1.0], 0.0, 1.0);
    entries(2) = build_static_source_summary( ...
        src, 'src_b', [0.0 1.0 0.0], [0.0 1.0 0.0], [0.0 0.0 1.0], 0.25, 0.5);

    verifyEqual(testCase, numel(entries), 2);
    verifyEqual(testCase, entries(1).source_name, 'src_a');
    verifyFalse(testCase, isfield(entries(2), 'reverberation'));
end
