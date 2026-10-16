function tests = test_prepare_receiver_source_audio
    tests = functiontests(localfunctions);
end

function testDownmixBeforeNormalizationAndGain(testCase)
    data = [1 0; 0 0.5; -1 -0.5; 0 0];
    source = make_audio(data);
    [prepared, policy] = prepare_receiver_source_audio(source, -6, true);
    expected = normalize_audio_peak(mean(data, 2), 0.99) * 10^(-6/20);
    verifyEqual(testCase, prepared.timeData, expected, 'AbsTol', 1e-12);
    verifyEqual(testCase, policy.original_num_channels, 2);
    verifyEqual(testCase, policy.downmix_policy, 'arithmetic_mean');
end

function testCatalogSourceRemainsStereo(testCase)
    data = [1 0; 0 0.5; -1 -0.5; 0 0];
    [prepared, policy] = prepare_receiver_source_audio(make_audio(data), 0, false);
    verifyEqual(testCase, prepared.timeData, normalize_audio_peak(data, 0.99));
    verifyEqual(testCase, policy.downmix_policy, 'none');
end

function testMonoOrStereoFeedsAllReceiverResponses(testCase)
    for source_channels = [1 2]
        for channels = [2 4 8]
            [source, policy] = prepare_receiver_source_audio(make_audio(ones(4,source_channels)), -20, true);
            filter = make_audio(zeros(12,channels));
            filter.signalType = 'energy';
            data = filter.timeData;
            for k = 1:channels
                data(k+1,k) = 1/k;
            end
            filter.timeData = data;
            convolved = ita_convolve(source, filter);
            verifyEqual(testCase, convolved.nChannels, channels);
            for k = 1:channels
                [peak, onset] = max(abs(convolved.timeData(:,k)));
                verifyEqual(testCase, peak, 0.099/k, 'AbsTol', 1e-10);
                verifyGreaterThanOrEqual(testCase, onset, k+1);
            end
            verifyEqual(testCase, policy.original_num_channels, source_channels);
        end
    end
end

function audio = make_audio(data)
    audio = itaAudio();
    audio.timeData = data;
    audio.samplingRate = 44100;
end
