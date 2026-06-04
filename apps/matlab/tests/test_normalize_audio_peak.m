function tests = test_normalize_audio_peak
    tests = functiontests(localfunctions);
end

function testNormalizesPeakToDefaultTarget(testCase)
    audio = [0.5 -0.25; -2.0 1.0; 0.1 0.2];

    normalized = normalize_audio_peak(audio);

    verifyEqual(testCase, max(abs(normalized), [], 'all'), 0.99, 'AbsTol', 1e-12);
end

function testLeavesSilentAudioUntouched(testCase)
    audio = zeros(4, 2);

    normalized = normalize_audio_peak(audio);

    verifyEqual(testCase, normalized, audio);
end

function testSupportsExplicitTargetPeak(testCase)
    audio = [0.2; -0.4; 0.1];

    normalized = normalize_audio_peak(audio, 0.75);

    verifyEqual(testCase, max(abs(normalized), [], 'all'), 0.75, 'AbsTol', 1e-12);
end
