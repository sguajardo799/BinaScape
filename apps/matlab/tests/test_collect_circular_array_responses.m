function tests = test_collect_circular_array_responses
    tests = functiontests(localfunctions);
end

function testSourceAndMicrophoneMappingPreservesDelaysAndLevels(testCase)
    rpf = make_responses();
    responses = collect_circular_array_responses(rpf, 2, 4, 44100);
    verifySize(testCase, responses, [1 2]);
    for s = 1:2
        verifyEqual(testCase, responses(s).nChannels, 4);
        for k = 1:4
            verifyEqual(testCase, responses(s).timeData(:,k), rpf.impulse_responses(s,k).timeData);
            verifyEqual(testCase, responses(s).channelNames{k}, sprintf('mic_%d', k));
        end
    end
end

function testRejectsShapeOrChannelMismatch(testCase)
    rpf = make_responses();
    verifyError(testCase, @() collect_circular_array_responses(rpf, 2, 8, 44100), ...
        'BinaScape:Matlab:ArrayResponseShape');
    rpf.impulse_responses(1,1).timeData = ones(12,2);
    verifyError(testCase, @() collect_circular_array_responses(rpf, 2, 4, 44100), ...
        'BinaScape:Matlab:ArrayResponseShape');
end

function rpf = make_responses()
    rpf = MockRavenProject();
    raw = itaAudio(2, 4);
    for s = 1:2
        for k = 1:4
            data = zeros(12,1);
            data(s+k) = 10*s+k;
            raw(s,k).timeData = data;
            raw(s,k).samplingRate = 44100;
            raw(s,k).channelNames{1} = sprintf('mic_%d', k);
        end
    end
    rpf.impulse_responses = raw;
end
