function tests = test_emit_render_event
    tests = functiontests(localfunctions);
end

function testEventsAreCompleteVersionedLines(testCase)
    root = tempname;
    mkdir(root);
    cleanup = onCleanup(@() rmdir(root, 's')); %#ok<NASGU>
    context = struct('attempt_id', 'unit-sequence', 'events_path', fullfile(root, 'events.jsonl'));
    emit_render_event(context, 'stage_started', 'initialization');
    emit_render_event(context, 'step_started', 'simulation', struct('step', 'raven_run'));
    text = fileread(context.events_path);
    verifyEqual(testCase, text(end), newline);
    lines = splitlines(strtrim(text));
    first = jsondecode(lines{1});
    second = jsondecode(lines{2});
    verifyEqual(testCase, first.schema_version, 1);
    verifyEqual(testCase, first.attempt_id, context.attempt_id);
    verifyEqual(testCase, second.sequence, first.sequence + 1);
    verifyEqual(testCase, second.step, 'raven_run');
    verifyTrue(testCase, endsWith(first.timestamp, 'Z'));
end

function testNoContextIsNoop(testCase)
    verifyWarningFree(testCase, @() emit_render_event([], 'stage_started', 'initialization'));
end

function testUnwritableDestinationAndWarningAsErrorAreContained(testCase)
    context = struct('attempt_id', 'unit-unwritable', ...
        'events_path', fullfile(tempname, 'missing', 'events.jsonl'));
    state = warning('query', 'BinaScape:Diagnostics:Unavailable');
    cleanup = onCleanup(@() warning(state)); %#ok<NASGU>
    warning('error', 'BinaScape:Diagnostics:Unavailable');
    emit_render_event(context, 'error', '', struct('error', struct('message', 'original')));
    verifyFalse(testCase, isfile(context.events_path));
end
