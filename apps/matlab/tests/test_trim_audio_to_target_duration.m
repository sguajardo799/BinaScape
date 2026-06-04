function tests = test_trim_audio_to_target_duration
    tests = functiontests(localfunctions);
end

function testTrimsWhenAudioExceedsTarget(testCase)
    audio = reshape(1:20, [10 2]);

    trimmed = trim_audio_to_target_duration(audio, 4, 1.5);

    verifySize(testCase, trimmed, [6 2]);
    verifyEqual(testCase, trimmed, audio(1:6, :));
end

function testPadsAudioWithZerosWhenTargetIsLonger(testCase)
    audio = reshape(1:12, [6 2]);

    trimmed = trim_audio_to_target_duration(audio, 4, 2.0);

    expected = [audio; zeros(2, 2)];

    verifySize(testCase, trimmed, [8 2]);
    verifyEqual(testCase, trimmed, expected);
end

function testLeavesAudioWhenTargetMissing(testCase)
    audio = reshape(1:12, [6 2]);

    trimmed = trim_audio_to_target_duration(audio, 4, []);

    verifyEqual(testCase, trimmed, audio);
end
