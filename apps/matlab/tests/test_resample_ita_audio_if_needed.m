function tests = test_resample_ita_audio_if_needed
    tests = functiontests(localfunctions);
end

function testMatchingRateDoesNotCallResampler(testCase)
    audio = struct('samplingRate', 44100, 'timeData', (1:4).');

    result = resample_ita_audio_if_needed(audio, 44100, 1, @failing_resampler);

    verifyEqual(testCase, result.samplingRate, 44100);
    verifyEqual(testCase, result.timeData, audio.timeData);
end

function testDifferentRateUsesItaResampleCompatibleFunction(testCase)
    audio = struct('samplingRate', 48000, 'timeData', (1:4).');

    result = resample_ita_audio_if_needed(audio, 44100, 1, @fake_ita_resample);

    verifyEqual(testCase, result.samplingRate, 44100);
    verifyEqual(testCase, result.requestedSamplingRate, 44100);
    verifyEqual(testCase, result.originalSamplingRate, 48000);
end

function testResamplerMustReturnTargetRate(testCase)
    audio = struct('samplingRate', 48000, 'timeData', (1:4).');

    verifyError(testCase, ...
        @() resample_ita_audio_if_needed(audio, 44100, 1, @wrong_rate_resampler), ...
        'BinaScape:Matlab:ResampleFailed');
end

function audio = fake_ita_resample(audio, target_fs)
    audio.originalSamplingRate = audio.samplingRate;
    audio.requestedSamplingRate = target_fs;
    audio.samplingRate = target_fs;
end

function audio = wrong_rate_resampler(audio, ~)
    audio.samplingRate = 48000;
end

function audio = failing_resampler(audio, ~)
    error('test_resample_ita_audio_if_needed:unexpectedCall', ...
        'Resampler should not be called when sample rates already match.');
end
