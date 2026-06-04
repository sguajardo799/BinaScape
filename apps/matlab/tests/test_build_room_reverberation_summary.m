function tests = test_build_room_reverberation_summary
    tests = functiontests(localfunctions);
end

function testStoresRawArrayAndMeanFromRoomT30(testCase)
    summary = build_room_reverberation_summary([0.6 0.9 1.2]);

    verifyEqual(testCase, summary.metric, 'T30');
    verifyEqual(testCase, summary.estimated_from, 'room');
    verifyEqual(testCase, summary.method, 'rpf.getT30');
    verifyEqual(testCase, summary.t30_s, [0.6 0.9 1.2], 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.mean_t30_s, 0.9, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.status, 'estimated');
end

function testIgnoresInvalidValuesWhenComputingMean(testCase)
    summary = build_room_reverberation_summary([0.5 NaN -1.0 Inf 1.0]);

    verifyEqual(testCase, summary.mean_t30_s, 0.75, 'AbsTol', 1e-12);
    verifyEqual(testCase, summary.status, 'estimated');
end

function testMarksUnavailableWhenRoomT30IsNotNumeric(testCase)
    summary = build_room_reverberation_summary('invalid');

    verifyTrue(testCase, isempty(summary.t30_s));
    verifyTrue(testCase, isempty(summary.mean_t30_s));
    verifyEqual(testCase, summary.status, 'unavailable');
end
