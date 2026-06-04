function tests = test_build_sources_from_config
    tests = functiontests(localfunctions);
end

function testAppliesOptionalSourceDirectivities(testCase)
    ctx = struct();
    ctx.manifest = struct('sources', [ ...
        make_source_cfg('src_01', 'C:/directivity/one.daff', [1.0 2.0 3.0], 0.0), ...
        make_source_cfg('src_02', '', [4.0 5.0 6.0], 45.0), ...
        make_source_cfg('src_03', 'C:/directivity/two.daff', [7.0 8.0 9.0], -30.0) ...
    ]);
    ctx.rpf = MockRavenProject();

    ctx = build_sources_from_config(ctx);

    verifyEqual(testCase, ctx.sourceNames, {'src_01', 'src_02', 'src_03'});
    verifyEqual(testCase, ctx.rpf.source_positions, [1.0 2.0 3.0; 4.0 5.0 6.0; 7.0 8.0 9.0]);
    verifyEqual(testCase, ctx.sourceDirectivityPaths, {'C:/directivity/one.daff', '', 'C:/directivity/two.daff'});
    verifyEqual(testCase, ctx.rpf.directivity_calls, {'C:/directivity/one.daff', 'C:/directivity/two.daff'});
    verifyEqual(testCase, ctx.sources(1).directivity_path, 'C:/directivity/one.daff');
    verifyEqual(testCase, ctx.sources(2).directivity_path, '');
end

function testKeepsExistingManifestsWithoutDirectivity(testCase)
    ctx = struct();
    ctx.manifest = struct('sources', make_source_cfg('src_legacy', '', [0.0 0.0 0.0], 90.0));
    ctx.manifest.sources = rmfield(ctx.manifest.sources, 'directivity_path');
    ctx.rpf = MockRavenProject();

    ctx = build_sources_from_config(ctx);

    verifyEqual(testCase, ctx.sourceDirectivityPaths, {''});
    verifyEmpty(testCase, ctx.rpf.directivity_calls);
    verifyEqual(testCase, ctx.sources.directivity_path, '');
end

function source = make_source_cfg(source_id, directivity_path, position_m, yaw_deg)
    source = struct( ...
        'source_id', source_id, ...
        'event_type', 'speech', ...
        'audio_path', 'C:/audio/event.wav', ...
        'directivity_path', directivity_path, ...
        'position_m', position_m, ...
        'orientation_deg', struct('yaw', yaw_deg, 'pitch', 0.0, 'roll', 0.0), ...
        'gain_db', 0.0, ...
        'start_time_s', 0.0);
end
