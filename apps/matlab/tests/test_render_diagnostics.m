function tests = test_render_diagnostics
    tests = functiontests(localfunctions);
end

function setup(testCase)
    global DIAGNOSTIC_TEST_CFG DIAGNOSTIC_TEST_FAILURE
    DIAGNOSTIC_TEST_FAILURE = '';
    root = tempname;
    mkdir(root);
    testCase.TestData.root = root;
    testCase.TestData.previous_directory = pwd;
    testCase.TestData.context = struct('attempt_id', char(java.util.UUID.randomUUID), ...
        'events_path', fullfile(root, 'events.jsonl'));
    output = struct('project_name', 'test-project', 'wav_path', fullfile(root, 'test.wav'), ...
        'metadata_path', fullfile(root, 'test.json'));
    DIAGNOSTIC_TEST_CFG = struct('render', struct('sample_rate_hz', 44100, ...
        'effective_seed', 1, 'seed_source', 'test', 'outputs', output), ...
        'receiver', struct('hrtfs', struct('hrtf_id', 'test', 'hrtf_path', 'test.daff')), ...
        'background_noise', []);
    write_stub(root, 'load_scene_config', [
        "function cfg = load_scene_config(varargin)"
        "global DIAGNOSTIC_TEST_CFG DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'load'), error('Test:Load', 'load failed'); end"
        "cfg = DIAGNOSTIC_TEST_CFG;"
        "end"]);
    write_stub(root, 'prepare_raven_project', [
        "function ctx = prepare_raven_project(cfg, varargin)"
        "global DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'prepare'), error('Test:Prepare', 'prepare failed'); end"
        "ctx = struct('rpf', DiagnosticsRavenStub(), 'n_sources', 0, 'manifest', cfg);"
        "end"]);
    for name = ["build_room_from_config", "build_receiver_from_pose", "build_sources_from_config"]
        write_stub(root, name, ["function ctx = " + name + "(ctx)"; "end"]);
    end
    write_stub(root, 'apply_background_noise', [
        "function [mix, summary] = apply_background_noise(mix, varargin)"
        "summary = struct();"
        "end"]);
    write_stub(root, 'write_output_wav', [
        "function write_output_wav(varargin)"
        "global DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'wav'), error('Test:Wav', 'wav failed'); end"
        "end"]);
    write_stub(root, 'export_render_metadata', [
        "function export_render_metadata(varargin)"
        "global DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'metadata'), error('Test:Metadata', 'metadata failed'); end"
        "end"]);
    write_stub(root, 'DiagnosticsRavenStub', [
        "classdef DiagnosticsRavenStub < handle"
        "properties, filterLength = 0; end"
        "methods"
        "function setGenerateRIR(varargin), end"
        "function setGenerateBRIR(varargin), end"
        "function setSimulationTypeRT(varargin), end"
        "function setSimulationTypeIS(varargin), end"
        "function setNumParticles(varargin), end"
        "function setISOrder_PS(varargin), end"
        "function setFixReflectionPattern(varargin), end"
        "function setFixPoissonSequence(varargin), end"
        "function run(varargin)"
        "global DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'run'), error('Test:Run', 'run failed'); end"
        "end"
        "function responses = getBinauralImpulseResponseItaAudio(varargin)"
        "global DIAGNOSTIC_TEST_FAILURE"
        "if strcmp(DIAGNOSTIC_TEST_FAILURE, 'audio'), error('Test:Audio', 'audio failed'); end"
        "responses = [];"
        "end"
        "function t30 = getT30(varargin), t30 = ones(1, 10); end"
        "end"
        "end"]);
    % El directorio actual precede al addpath del entrypoint; los stubs no viven en genpath.
    cd(root);
end

function teardown(testCase)
    cd(testCase.TestData.previous_directory);
    clear load_scene_config prepare_raven_project build_room_from_config build_receiver_from_pose
    clear build_sources_from_config apply_background_noise write_output_wav export_render_metadata DiagnosticsRavenStub
    rmdir(testCase.TestData.root, 's');
    clear global DIAGNOSTIC_TEST_CFG DIAGNOSTIC_TEST_FAILURE
end

function testSuccessStageAndStepOrder(testCase)
    run_raven_static_render('stub-input.json', testCase.TestData.context);
    events = read_events(testCase);
    types = cellfun(@(e) e.type, events, 'UniformOutput', false);
    verifyEqual(testCase, types, {'stage_started'; 'stage_completed'; 'stage_started'; ...
        'step_started'; 'step_completed'; 'step_started'; 'step_completed'; ...
        'stage_completed'; 'stage_started'; 'artifact_written'; 'artifact_written'; ...
        'stage_completed'; 'entrypoint_completed'});
    step_events = events(startsWith(types, 'step_'));
    verifyEqual(testCase, cellfun(@(e) e.step, step_events, 'UniformOutput', false), ...
        {'raven_run'; 'raven_run'; 'audio_postprocessing'; 'audio_postprocessing'});
end

function testFailuresDoNotCompleteActiveStage(testCase)
    global DIAGNOSTIC_TEST_FAILURE
    cases = {'load', 'prepare', 'run', 'audio'};
    identifiers = {'Test:Load', 'Test:Prepare', 'Test:Run', 'Test:Audio'};
    for i = 1:numel(cases)
        if isfile(testCase.TestData.context.events_path), delete(testCase.TestData.context.events_path); end
        DIAGNOSTIC_TEST_FAILURE = cases{i};
        verifyError(testCase, @() run_raven_static_render('stub-input.json', ...
            testCase.TestData.context), identifiers{i});
        events = read_events(testCase);
        verifyEqual(testCase, events{end}.type, 'error');
        verifyEqual(testCase, events{end}.error.identifier, identifiers{i});
        verifyFalse(testCase, any(cellfun(@(e) strcmp(e.type, 'entrypoint_completed'), events)));
        steps = events(cellfun(@(e) startsWith(e.type, 'step_'), events));
        if strcmp(cases{i}, 'run')
            verifyEqual(testCase, numel(steps), 1);
            verifyEqual(testCase, steps{end}.step, 'raven_run');
        elseif strcmp(cases{i}, 'audio')
            verifyEqual(testCase, numel(steps), 3);
            verifyEqual(testCase, steps{end}.step, 'audio_postprocessing');
        else
            verifyEmpty(testCase, steps);
        end
    end
end

function testWavAndMetadataFailuresKeepOriginalError(testCase)
    global DIAGNOSTIC_TEST_FAILURE
    for mode = ["wav", "metadata"]
        if isfile(testCase.TestData.context.events_path), delete(testCase.TestData.context.events_path); end
        DIAGNOSTIC_TEST_FAILURE = char(mode);
        if mode == "wav"
            identifier = 'Test:Wav';
            writer = 'write_output_wav';
        else
            identifier = 'Test:Metadata';
            writer = 'export_render_metadata';
        end
        try
            run_raven_static_render('stub-input.json', testCase.TestData.context);
            verifyFail(testCase, 'Expected writer error');
        catch ME
            verifyEqual(testCase, ME.identifier, identifier);
            verifyTrue(testCase, contains(ME.stack(1).name, writer));
        end
        events = read_events(testCase);
        writes = events(cellfun(@(e) strcmp(e.type, 'artifact_written'), events));
        if mode == "wav"
            verifyEmpty(testCase, writes);
        else
            verifyEqual(testCase, numel(writes), 1);
            verifyEqual(testCase, writes{1}.kind, 'wav');
            verifyEqual(testCase, writes{1}.output_type, 'test');
        end
        verifyEqual(testCase, events{end}.type, 'error');
        verifyEqual(testCase, events{end-1}.stage, 'artifact_writing');
        verifyNotEqual(testCase, events{end-1}.type, 'stage_completed');
    end
end

function testLegacyAndMultiVariantCallsPreserveResults(testCase)
    global DIAGNOSTIC_TEST_CFG
    DIAGNOSTIC_TEST_CFG.receiver.hrtfs(2) = struct('hrtf_id', 'second', 'hrtf_path', 'second.daff');
    second = DIAGNOSTIC_TEST_CFG.render.outputs(1);
    second.project_name = 'test-second';
    second.wav_path = fullfile(testCase.TestData.root, 'second.wav');
    second.metadata_path = fullfile(testCase.TestData.root, 'second.json');
    DIAGNOSTIC_TEST_CFG.render.outputs(2) = second;
    before = render_static_scene(DIAGNOSTIC_TEST_CFG);
    after = render_static_scene(DIAGNOSTIC_TEST_CFG, testCase.TestData.context);
    verifyEqual(testCase, after, before);
    delete(testCase.TestData.context.events_path);
    run_raven_static_render('stub-input.json');
    verifyFalse(testCase, isfile(testCase.TestData.context.events_path));
    run_raven_static_render('stub-input.json', testCase.TestData.context);
    events = read_events(testCase);
    steps = events(cellfun(@(e) startsWith(e.type, 'step_'), events));
    verifyEqual(testCase, cellfun(@(e) e.output_type, steps, 'UniformOutput', false), ...
        {'test'; 'test'; 'test'; 'test'; 'second'; 'second'; 'second'; 'second'});
    writes = events(cellfun(@(e) strcmp(e.type, 'artifact_written'), events));
    verifyEqual(testCase, cellfun(@(e) e.output_type, writes, 'UniformOutput', false), ...
        {'test'; 'test'; 'second'; 'second'});
    verifyEqual(testCase, numel(unique(cellfun(@(e) e.sequence, events))), numel(events));
end

function testDiagnosticAndWarningFailuresPreserveOriginalException(testCase)
    global DIAGNOSTIC_TEST_FAILURE
    DIAGNOSTIC_TEST_FAILURE = 'run';
    original = capture_error(@() run_raven_static_render('stub-input.json'));
    broken = testCase.TestData.context;
    broken.events_path = fullfile(testCase.TestData.root, 'missing-directory', 'events.jsonl');
    state = warning('query', 'BinaScape:Diagnostics:Unavailable');
    cleanup = onCleanup(@() warning(state)); %#ok<NASGU>
    warning('error', 'BinaScape:Diagnostics:Unavailable');
    % El helper falla también cuando el catch intenta registrar la excepción original.
    actual = capture_error(@() run_raven_static_render('stub-input.json', broken));
    verifyEqual(testCase, actual.identifier, original.identifier);
    verifyEqual(testCase, actual.message, original.message);
    verifyEqual(testCase, actual.stack(1), original.stack(1));
    DIAGNOSTIC_TEST_FAILURE = '';
    run_raven_static_render('stub-input.json', broken);
    verifyFalse(testCase, isfile(broken.events_path));
end

function caught = capture_error(action)
    caught = [];
    try
        action();
    catch ME
        caught = ME;
    end
end

function events = read_events(testCase)
    lines = splitlines(strtrim(fileread(testCase.TestData.context.events_path)));
    events = cellfun(@jsondecode, lines, 'UniformOutput', false);
end

function write_stub(root, name, lines)
    fid = fopen(fullfile(root, char(name + ".m")), 'w');
    cleanup = onCleanup(@() fclose(fid)); %#ok<NASGU>
    fprintf(fid, '%s\n', lines);
end
