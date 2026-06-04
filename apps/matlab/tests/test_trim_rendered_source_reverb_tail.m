function tests = test_trim_rendered_source_reverb_tail
    tests = functiontests(localfunctions);
end

function testRemovesFilterTailFromRenderedAudio(testCase)
    audio = reshape(1:12, [6 2]);

    trimmed = trim_rendered_source_reverb_tail(audio, 4);

    verifyEqual(testCase, trimmed, audio(1:3, :));
end

function testKeepsAudioUntouchedWhenFilterLengthIsOne(testCase)
    audio = reshape(1:12, [6 2]);

    trimmed = trim_rendered_source_reverb_tail(audio, 1);

    verifyEqual(testCase, trimmed, audio);
end

function testAllowsRenderedAudioToBecomeEmpty(testCase)
    audio = reshape(1:6, [3 2]);

    trimmed = trim_rendered_source_reverb_tail(audio, 5);

    verifyEqual(testCase, size(trimmed), [0 2]);
end
