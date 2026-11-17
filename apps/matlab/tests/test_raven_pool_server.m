function tests = test_raven_pool_server
    tests = functiontests(localfunctions);
end

function setup(testCase)
    root = fullfile(tempdir, 'binascape-pool-test', char(java.util.UUID.randomUUID));
    mkdir(fullfile(root, 'requests'));
    testCase.TestData.root = root;
    testCase.addTeardown(@() rmdir(root, 's'));
end

function testSuccessfulJobPublishesExitZeroAndConsole(testCase)
    root = testCase.TestData.root;
    publish_request(root, 'aaaa', @(varargin) []);
    run_until_results(root, 1, @(s, d, c) fprintf('rendering %s\n', s));
    result = read_result(root, 'aaaa');
    verifyEqual(testCase, result.exit_code, 0);
    verifyEmpty(testCase, result.error);
    verifyNotEmpty(testCase, result.worker_id);
    verifyNotEmpty(testCase, result.started_at);
    verifyNotEmpty(testCase, result.finished_at);
    verifySubstring(testCase, fileread(fullfile(root, 'aaaa.console.log')), 'rendering snap-aaaa');
    verifyTrue(testCase, isfile(fullfile(root, 'ready.json')));
end

function testThrowingJobPublishesNonZeroExitAndMessage(testCase)
    root = testCase.TestData.root;
    publish_request(root, 'bbbb', []);
    run_until_results(root, 1, @(s, d, c) error('BinaScape:Test:Boom', 'boom for %s', s));
    result = read_result(root, 'bbbb');
    verifyNotEqual(testCase, result.exit_code, 0);
    verifySubstring(testCase, result.error.message, 'boom for snap-bbbb');
end

function testUnreadableRequestFailsWithoutStoppingOthers(testCase)
    root = testCase.TestData.root;
    write_text(fullfile(root, 'requests', 'cccc.json'), '{not json');
    publish_request(root, 'dddd', []);
    run_until_results(root, 2, @(s, d, c) []);
    verifyNotEqual(testCase, read_result(root, 'cccc').exit_code, 0);
    verifyEqual(testCase, read_result(root, 'dddd').exit_code, 0);
end

function testDiagnosticsAndCompletionContextArePassedThrough(testCase)
    root = testCase.TestData.root;
    publish_request(root, 'eeee', []);
    seen = fullfile(root, 'seen.json');
    run_until_results(root, 1, @(s, d, c) write_text(seen, jsonencode(struct('d', d, 'c', c))));
    passed = jsondecode(fileread(seen));
    verifyEqual(testCase, passed.d.attempt_id, 'eeee');
    verifyEqual(testCase, passed.c.variant_id, 'scene__binaural_hrtf');
end

function testStopEndsServerAndResultsAreCompleteJson(testCase)
    root = testCase.TestData.root;
    for k = 1:3
        publish_request(root, sprintf('f%03d', k), []);
    end
    run_until_results(root, 3, @(s, d, c) pause(0.2));
    listing = dir(fullfile(root, 'results', '*'));
    names = {listing(~[listing.isdir]).name};
    verifyEqual(testCase, sort(names), {'f001.json', 'f002.json', 'f003.json'});
    for k = 1:numel(names)
        verifyClass(testCase, jsondecode(fileread(fullfile(root, 'results', names{k}))), 'struct');
    end
end

% --- helpers -----------------------------------------------------------------

function publish_request(root, attempt_id, ~)
    request = struct('attempt_id', attempt_id, 'snapshot_path', ['snap-' attempt_id], ...
        'diagnostics', struct('attempt_id', attempt_id, 'events_path', ''), ...
        'completion_context', struct('attempt_id', attempt_id, 'variant_id', 'scene__binaural_hrtf'), ...
        'console_path', fullfile(root, [attempt_id '.console.log']));
    write_text(fullfile(root, 'requests', [attempt_id '.json']), jsonencode(request));
end

function run_until_results(root, expected, render_fn)
    % A timer writes `stop` once the expected results exist; timers fire during
    % the server loop's pause().
    stopper = timer('ExecutionMode', 'fixedSpacing', 'Period', 0.2, 'TimerFcn', ...
        @(~, ~) stop_when_done(root, expected));
    cleanup = onCleanup(@() stop_and_delete(stopper));
    start(stopper);
    options = struct('render_fn', render_fn, 'pool_factory', @(n) backgroundPool, ...
        'prepare_workers', @(pool) [], 'poll_s', 0.05, 'heartbeat_s', 0.1);
    run_raven_pool_server(root, 2, 0, options);
end

function stop_when_done(root, expected)
    if numel(dir(fullfile(root, 'results', '*.json'))) >= expected
        write_text(fullfile(root, 'stop'), '');
    end
end

function result = read_result(root, attempt_id)
    result = jsondecode(fileread(fullfile(root, 'results', [attempt_id '.json'])));
end

function write_text(path, text)
    fid = fopen(path, 'w', 'n', 'UTF-8');
    fwrite(fid, text, 'char');
    fclose(fid);
end

function stop_and_delete(stopper)
    stop(stopper);
    delete(stopper);
end
