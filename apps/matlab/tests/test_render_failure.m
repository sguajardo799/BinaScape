function tests = test_render_failure
    tests = functiontests(localfunctions);
end

function setup(testCase)
    root = fullfile('C:/Users/sguaj/AppData/Local/Temp/opencode', ...
        char(java.util.UUID.randomUUID));
    mkdir(root);
    testCase.TestData.root = root;
    testCase.TestData.previous = pwd;
    manifest = fullfile(root, 'input_manifest.json');
    write_text(manifest, '{invalid json');
    testCase.TestData.manifest = manifest;
    testCase.TestData.context = struct('attempt_id', repmat('a', 1, 32), ...
        'variant_id', 'scene__hrtf', 'manifest_sha256', sha256_file(manifest), ...
        'completion_path', fullfile(root, 'completion.json'), ...
        'failure_path', fullfile(root, 'failure.json'));
end

function teardown(testCase)
    cd(testCase.TestData.previous);
    clear load_scene_config render_static_scene
    rmdir(testCase.TestData.root, 's');
end

function testValidationFailurePublishesBoundReport(testCase)
    data = testCase.TestData;
    context = data.context;
    context.scene_id = 'scene';
    context.output_type = 'binaural_hrtf';
    original = capture(@() load_scene_config(data.manifest, @validate_static_scene_config));
    actual = capture(@() run_raven_static_render(data.manifest, [], context));
    verifyEqual(testCase, actual.identifier, original.identifier);
    verifyEqual(testCase, actual.message, original.message);
    report = jsondecode(fileread(context.failure_path));
    verifyEqual(testCase, report.schema_version, 1);
    for field = {'attempt_id', 'variant_id', 'manifest_sha256', 'scene_id', 'output_type'}
        verifyEqual(testCase, report.(field{1}), context.(field{1}));
    end
    verifyEqual(testCase, report.category, 'invalid_input');
    verifyEqual(testCase, report.error.identifier, original.identifier);
    verifyEqual(testCase, report.error.message, original.message);
    verifyFalse(testCase, isfile(context.completion_path));
    verifyFalse(testCase, isfile([context.failure_path '.tmp']));
end

function testLegacySignaturesPreserveOriginalFailure(testCase)
    data = testCase.TestData;
    original = capture(@() load_scene_config(data.manifest, @validate_static_scene_config));
    old = rmfield(data.context, 'failure_path');
    calls = {@() run_raven_static_render(data.manifest), ...
        @() run_raven_static_render(data.manifest, []), ...
        @() run_raven_static_render(data.manifest, [], old)};
    for i = 1:numel(calls)
        actual = capture(calls{i});
        verifyEqual(testCase, actual.identifier, original.identifier);
        verifyEqual(testCase, actual.message, original.message);
    end
    verifyFalse(testCase, isfile(data.context.failure_path));
    verifyFalse(testCase, isfile(old.completion_path));
end

function testRealValidatorFailureAndDiagnosticRemainIndependent(testCase)
    data = testCase.TestData;
    write_text(data.manifest, '{}');
    context = data.context;
    context.manifest_sha256 = sha256_file(data.manifest);
    diagnostics = struct('attempt_id', context.attempt_id, ...
        'events_path', fullfile(data.root, 'events.jsonl'));
    original = capture(@() load_scene_config(data.manifest, @validate_static_scene_config));
    actual = capture(@() run_raven_static_render(data.manifest, diagnostics, context));
    verifyEqual(testCase, actual.identifier, original.identifier);
    verifyEqual(testCase, actual.message, original.message);
    report = jsondecode(fileread(context.failure_path));
    verifyEqual(testCase, report.category, 'invalid_input');
    verifyFalse(testCase, isfield(report, 'scene_id'));
    events = splitlines(strtrim(fileread(diagnostics.events_path)));
    last = jsondecode(events{end});
    verifyEqual(testCase, last.type, 'error');
    verifyEqual(testCase, last.error.message, original.message);
    verifyFalse(testCase, contains(fileread(diagnostics.events_path), 'entrypoint_completed'));
    verifyFalse(testCase, isfile(context.completion_path));
end

function testMissingSnapshotCannotProduceBoundEvidence(testCase)
    data = testCase.TestData;
    delete(data.manifest);
    previous = warning('off', 'BinaScape:Failure:ReportFailed');
    cleanup = onCleanup(@() warning(previous)); %#ok<NASGU>
    original = capture(@() load_scene_config(data.manifest, @validate_static_scene_config));
    actual = capture(@() run_raven_static_render(data.manifest, [], data.context));
    verifyEqual(testCase, actual.message, original.message);
    verifyFalse(testCase, isfile(data.context.failure_path));
    verifyFalse(testCase, isfile(data.context.completion_path));
end

function testStorageAndWarningFailuresNeverReplaceOriginal(testCase)
    data = testCase.TestData;
    mkdir([data.context.failure_path '.tmp']);
    previous = warning('error', 'BinaScape:Failure:ReportFailed');
    cleanup = onCleanup(@() warning(previous)); %#ok<NASGU>
    original = capture(@() load_scene_config(data.manifest, @validate_static_scene_config));
    actual = capture(@() run_raven_static_render(data.manifest, [], data.context));
    verifyEqual(testCase, actual.identifier, original.identifier);
    verifyEqual(testCase, actual.message, original.message);
    verifyFalse(testCase, isfile(data.context.failure_path));
    verifyFalse(testCase, isfile(data.context.completion_path));
end

function testSimulationFailureIsNotInvalidInput(testCase)
    data = testCase.TestData;
    write_text(fullfile(data.root, 'load_scene_config.m'), ...
        sprintf('function cfg=load_scene_config(varargin)\ncfg=struct();\nend\n'));
    write_text(fullfile(data.root, 'render_static_scene.m'), ...
        sprintf('function result=render_static_scene(varargin)\nerror(''Test:Simulation'', ''simulation failed'');\nend\n'));
    cd(data.root);
    clear load_scene_config render_static_scene
    verifyError(testCase, @() run_raven_static_render(data.manifest, [], data.context), 'Test:Simulation');
    verifyFalse(testCase, isfile(data.context.failure_path));
    verifyFalse(testCase, isfile(data.context.completion_path));
end

function testIdentityHashAndExistingEvidenceAreProtected(testCase)
    data = testCase.TestData;
    original = MException('Test:Input', 'bad input');
    context = data.context;
    context.manifest_sha256 = repmat('0', 1, 64);
    verifyError(testCase, @() write_render_failure(data.manifest, context, original), ...
        'BinaScape:Failure:ManifestMismatch');
    context = data.context;
    context.attempt_id = 'foreign';
    verifyError(testCase, @() write_render_failure(data.manifest, context, original), ...
        'BinaScape:Failure:InvalidContext');
    write_render_failure(data.manifest, data.context, original);
    before = fileread(data.context.failure_path);
    verifyError(testCase, @() write_render_failure(data.manifest, data.context, original), ...
        'BinaScape:Failure:AlreadyExists');
    verifyEqual(testCase, fileread(data.context.failure_path), before);
end

function exception = capture(call)
    try
        call();
    catch exception
        return;
    end
    error('Test:ExpectedError', 'Expected an exception.');
end

function write_text(path, text)
    fid = fopen(path, 'wb');
    assert(fid ~= -1);
    cleanup = onCleanup(@() fclose(fid)); %#ok<NASGU>
    fwrite(fid, unicode2native(text, 'UTF-8'), 'uint8');
end
