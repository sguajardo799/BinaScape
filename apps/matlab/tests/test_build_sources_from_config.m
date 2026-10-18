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
    verifyEqual(testCase, ctx.sources.identity_id, '');
    verifyEqual(testCase, ctx.sources.audio_relpath, '');
end

function testPreservesIdentityForGroupedAndFlatSources(testCase)
    grouped = make_source_cfg('src_01', '', [1 2 3], 0);
    grouped.identity_id = "Subject Two";
    grouped.audio_relpath = "Subject Two/clip.wav";
    flat = make_source_cfg('src_02', '', [2 2 3], 0);
    flat.identity_id = '';
    flat.audio_relpath = 'clip_ID13.wav';
    ctx = struct('manifest', struct('sources', [grouped flat]), 'rpf', MockRavenProject());
    ctx = build_sources_from_config(ctx);
    verifyEqual(testCase, ctx.sources(1).identity_id, 'Subject Two');
    verifyEqual(testCase, ctx.sources(1).audio_relpath, 'Subject Two/clip.wav');
    verifyEqual(testCase, ctx.sources(2).identity_id, '');
    verifyEqual(testCase, ctx.sources(2).audio_relpath, 'clip_ID13.wav');
    verifyEqual(testCase, ctx.sourceNames, {'src_01', 'src_02'});
end

function testSchema2ReflectsPositionsAndConvertsPublicOrientationForRaven(testCase)
    ctx = struct();
    ctx.manifest = struct( ...
        'schema_version', '2.0', ...
        'sources', make_source_cfg('src_schema2', '', [1.0 2.0 3.0], 90.0));
    ctx.rpf = MockRavenProject();

    ctx = build_sources_from_config(ctx);

    verifyEqual(testCase, ctx.rpf.source_positions, [1.0 2.0 -3.0]);
    verifyEqual(testCase, ctx.rpf.source_view_vectors, [0.0 0.0 -1.0], 'AbsTol', 1e-12);
    verifyEqual(testCase, ctx.sources.position_m, [1.0 2.0 -3.0]);
end

function testSchema3ReflectsPositionsAndConvertsPublicOrientationForRaven(testCase)
    ctx = struct();
    ctx.manifest = struct( ...
        'schema_version', '3.0', ...
        'sources', make_source_cfg('src_schema3', '', [1.0 2.0 3.0], 90.0));
    ctx.rpf = MockRavenProject();

    ctx = build_sources_from_config(ctx);

    verifyEqual(testCase, ctx.rpf.source_positions, [1.0 2.0 -3.0]);
    verifyEqual(testCase, ctx.rpf.source_view_vectors, [0.0 0.0 -1.0], 'AbsTol', 1e-12);
    verifyEqual(testCase, ctx.sources.position_m, [1.0 2.0 -3.0]);
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
