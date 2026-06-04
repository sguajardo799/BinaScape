function tests = test_render_static_scene_multi_hrtf
    tests = functiontests(localfunctions);
end

function testPreallocatedRunsAcceptRenderedEntries(testCase)
    runs = preallocate_static_runs(2);

    runs(1) = make_run('subject-a', 'a.daff', 'out-a.wav', 'meta-a.json');
    runs(2) = make_run('subject-b', 'b.daff', 'out-b.wav', 'meta-b.json');

    verifyEqual(testCase, {runs.hrtf_id}, {'subject-a', 'subject-b'});
    verifyEqual(testCase, runs(2).output.project_name, 'project__subject-b');
    verifySize(testCase, runs(1).audio, [4 2]);
end

function run = make_run(hrtf_id, hrtf_path, wav_path, metadata_path)
    run = struct();
    run.hrtf_id = hrtf_id;
    run.hrtf_path = hrtf_path;
    run.output = struct('wav_path', wav_path, 'metadata_path', metadata_path, 'project_name', ['project__' hrtf_id]);
    run.audio = ones(4, 2);
    run.fs = 44100;
    run.summary = struct('hrtf_id', hrtf_id, 'output_wav_path', wav_path);
end
