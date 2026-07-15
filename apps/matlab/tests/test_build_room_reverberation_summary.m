function tests = test_build_room_reverberation_summary
    tests = functiontests(localfunctions);
end

function testStoresRawArrayAndMeanFromRoomT30(testCase)
    t30_values = [0.6 0.7 0.8 0.9 1.0 1.1 1.2 1.3 1.4 1.5];
    summary = build_room_reverberation_summary(t30_values);

    verifyEqual(testCase, summary.metric, 'T30');
    verifyEqual(testCase, summary.estimated_from, 'room');
    verifyEqual(testCase, summary.method, 'rpf.getT30');
    verifyEqual(testCase, summary.t30_s, t30_values, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.band_frequencies_hz, ...
        [31.5 63 125 250 500 1000 2000 4000 8000 16000]);
    verifyEqual(testCase, summary.band_resolution, 'octave');
    verifyEqual(testCase, summary.frequency_mapping, 'center_frequency');
    verifyEqual(testCase, summary.valid_band_count, 10);
    verifyEqual(testCase, summary.mean_t30_s, 1.05, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.status, 'estimated');
end

function testIgnoresInvalidValuesWhenComputingMean(testCase)
    summary = build_room_reverberation_summary([0.5 NaN -1.0 Inf 1.0 0.6 0.7 0.8 0.9 1.0]);

    verifyEqual(testCase, summary.mean_t30_s, 0.785714285714286, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.valid_band_count, 7);
    verifyEqual(testCase, summary.status, 'estimated');
end

function testMarksUnavailableWhenRoomT30IsNotNumeric(testCase)
    summary = build_room_reverberation_summary('invalid');

    verifyTrue(testCase, isempty(summary.t30_s));
    verifyTrue(testCase, isempty(summary.band_frequencies_hz));
    verifyEqual(testCase, summary.valid_band_count, 0);
    verifyTrue(testCase, isempty(summary.mean_t30_s));
    verifyEqual(testCase, summary.status, 'unavailable');
end

function testRejectsUnexpectedRavenT30BandCount(testCase)
    verifyError(testCase, @() build_room_reverberation_summary([0.5 0.6 0.7]), ...
        'BinaScape:Matlab:UnexpectedT30BandCount');
end
