function tests = test_validate_static_scene_config
    tests = functiontests(localfunctions);
end

function testCanonicalPluralList(testCase)
    cfg = make_base_cfg();
    cfg.receiver = rmfield(cfg.receiver, 'hrtf');
    cfg.receiver.hrtfs = [ ...
        struct('hrtf_id', 'Subject 01', 'hrtf_path', 'a.daff'), ...
        struct('hrtf_id', 'Subject 01', 'hrtf_path', 'b.daff') ...
    ];

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, numel(normalized.receiver.hrtfs), 2);
    verifyEqual(testCase, normalized.receiver.hrtfs(1).hrtf_id, 'subject-01');
    verifyEqual(testCase, normalized.receiver.hrtfs(2).hrtf_id, 'subject-01-2');
    verifyEqual(testCase, normalized.render.outputs(1).wav_path, 'out__subject-01.wav');
    verifyEqual(testCase, normalized.render.seed_source, 'render.seed');
    verifyEqual(testCase, normalized.render.effective_seed, 123);
end

function testRejectsEmptyPluralList(testCase)
    cfg = make_base_cfg();
    cfg.receiver = rmfield(cfg.receiver, 'hrtf');
    cfg.receiver.hrtfs = struct([]);

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testRejectsIncompleteEntries(testCase)
    cfg = make_base_cfg();
    cfg.receiver = rmfield(cfg.receiver, 'hrtf');
    cfg.receiver.hrtfs = struct('hrtf_id', 'only-id');

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testSingularCompatibility(testCase)
    cfg = make_base_cfg();
    cfg.render = rmfield(cfg.render, 'seed');
    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, numel(normalized.receiver.hrtfs), 1);
    verifyEqual(testCase, normalized.receiver.hrtfs(1).hrtf_id, 'subject-legacy');
    verifyEqual(testCase, normalized.render.effective_seed, 77);
    verifyEqual(testCase, normalized.render.seed_source, 'seed');
    verifyEqual(testCase, normalized.render.outputs(1).wav_path, 'out.wav');
end

function testConflictingSingularAndPlural(testCase)
    cfg = make_base_cfg();
    cfg.receiver.hrtfs = struct('hrtf_id', 'other', 'hrtf_path', 'other.daff');

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testRenderSeedPriority(testCase)
    cfg = make_base_cfg();
    cfg.seed = 99;
    cfg.render.seed = 123;

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.effective_seed, 123);
    verifyEqual(testCase, normalized.render.seed_source, 'render.seed');
end

function testRequiresRoomMaterialFiles(testCase)
    cfg = make_base_cfg();
    cfg.room = rmfield(cfg.room, 'material_files');

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testRejectsRelativeRoomMaterialPath(testCase)
    cfg = make_base_cfg();
    cfg.room.material_files.floor.material_path = 'relative/material.mat';

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testRejectsMismatchedRoomMaterialId(testCase)
    cfg = make_base_cfg();
    cfg.room.material_files.ceiling.material_id = 'different-material';

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testNormalizesRoomMaterialFiles(testCase)
    cfg = make_base_cfg();

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.room.materials.north_wall, 'bricks');
    verifyEqual(testCase, normalized.room.material_files.floor.material_id, 'wood');
    verifyTrue(testCase, isfile(normalized.room.material_files.ceiling.material_path));
end

function testNormalizesOptionalSourceDirectivityPath(testCase)
    cfg = make_base_cfg();
    cfg.sources.directivity_path = "C:/directivity/source-pattern.daff";

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.sources.directivity_path, 'C:/directivity/source-pattern.daff');
end

function testNormalizesMixedSourceFieldSetsFromCellArray(testCase)
    cfg = make_base_cfg();

    source_with_directivity = cfg.sources;
    source_with_directivity.directivity_path = "C:/directivity/source-pattern.daff";

    source_without_directivity = rmfield(source_with_directivity, 'directivity_path');
    source_without_directivity.source_id = 'src2';

    cfg.sources = {source_with_directivity, source_without_directivity};

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, numel(normalized.sources), 2);
    verifyEqual(testCase, normalized.sources(1).directivity_path, 'C:/directivity/source-pattern.daff');
    verifyEmpty(testCase, normalized.sources(2).directivity_path);
end

function testNormalizesTargetDuration(testCase)
    cfg = make_base_cfg();
    cfg.render.target_duration_s = single(10);

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.target_duration_s, 10);
end

function testDefaultsTrimReverbTailToFalse(testCase)
    cfg = make_base_cfg();

    normalized = validate_static_scene_config(cfg);

    verifyFalse(testCase, normalized.render.trim_reverb_tail);
end

function testNormalizesTrimReverbTailFlag(testCase)
    cfg = make_base_cfg();
    cfg.render.trim_reverb_tail = 1;

    normalized = validate_static_scene_config(cfg);

    verifyTrue(testCase, normalized.render.trim_reverb_tail);
end

function testRejectsInvalidTrimReverbTailFlag(testCase)
    cfg = make_base_cfg();
    cfg.render.trim_reverb_tail = 'yes';

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testRejectsNonPositiveTargetDuration(testCase)
    cfg = make_base_cfg();
    cfg.render.target_duration_s = 0;

    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function testDefaultsBackgroundNoiseDisabled(testCase)
    cfg = make_base_cfg();

    normalized = validate_static_scene_config(cfg);

    verifyFalse(testCase, normalized.background_noise.enabled);
    verifyEmpty(testCase, normalized.background_noise.layers);
end

function testNormalizesColoredBackgroundNoise(testCase)
    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'colored', 'color', 'pink', 'snr_db', 20, 'seed', 7));

    normalized = validate_static_scene_config(cfg);

    verifyTrue(testCase, normalized.background_noise.enabled);
    verifyEqual(testCase, normalized.background_noise.layers.strategy, 'colored');
    verifyEqual(testCase, normalized.background_noise.layers.strategy_original, 'colored');
    verifyEqual(testCase, normalized.background_noise.layers.color, 'pink');
end

function testNormalizesAudioFolderAliasToAudioFile(testCase)
    cfg = make_base_cfg();
    wav_path = make_temp_wav(testCase);
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'audio_folder', 'path', wav_path, 'snr_db', 10));

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.background_noise.layers.strategy_original, 'audio_folder');
    verifyEqual(testCase, normalized.background_noise.layers.strategy, 'audio_file');
    verifyEqual(testCase, normalized.background_noise.layers.path, wav_path);
end

function testRejectsInvalidBackgroundNoiseFields(testCase)
    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'colored', 'color', 'green', 'snr_db', 10));
    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));

    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'colored', 'color', 'white', 'snr_db', Inf));
    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));

    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'colored', 'color', 'white', 'snr_db', 10, 'seed', 1.5));
    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));

    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'audio_file', 'path', fullfile(tempdir, 'missing.wav'), 'snr_db', 10));
    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));

    cfg = make_base_cfg();
    cfg.background_noise = struct('enabled', true, 'layers', struct( ...
        'strategy', 'audio_folder', 'path', tempdir, 'snr_db', 10));
    verifyThrowsAny(testCase, @() validate_static_scene_config(cfg));
end

function cfg = make_base_cfg()
    repo_root = get_repo_root();
    cfg = struct();
    cfg.schema_version = '1.0';
    cfg.scene_type = 'static';
    cfg.scene_id = 'scene';
    cfg.job_id = 'job';
    cfg.base_rpf_file = 'base.rpf';
    cfg.project_name = 'project';
    cfg.seed = 77;
    cfg.room = make_room_cfg(repo_root);
    cfg.receiver = struct();
    cfg.receiver.position_m = [1.0 1.0 1.0];
    cfg.receiver.orientation_deg = struct('yaw', 0.0, 'pitch', 0.0, 'roll', 0.0);
    cfg.receiver.hrtf = struct('hrtf_id', 'Subject Legacy', 'hrtf_path', 'legacy.daff');
    cfg.sources = struct('source_id', 'src1', 'audio_path', 'a.wav', 'position_m', [0 0 0], ...
        'orientation_deg', struct('yaw', 0.0, 'pitch', 0.0, 'roll', 0.0));
    cfg.render = struct('sample_rate_hz', 44100, 'output_wav_path', 'out.wav', ...
        'output_metadata_path', 'out.json', 'seed', 123);
end

function room = make_room_cfg(repo_root)
    room = struct();
    room.dimensions_m = [4.0 5.0 3.0];
    room.materials = struct( ...
        'north_wall', 'bricks', ...
        'south_wall', 'bricks', ...
        'east_wall', 'glass', ...
        'west_wall', 'wood', ...
        'floor', 'wood', ...
        'ceiling', 'plaster');
    room.material_files = struct( ...
        'north_wall', struct('material_id', 'bricks', 'material_path', fullfile(repo_root, 'assets', 'materials', 'bricks', 'Bricks.mat')), ...
        'south_wall', struct('material_id', 'bricks', 'material_path', fullfile(repo_root, 'assets', 'materials', 'bricks', 'Bricks.mat')), ...
        'east_wall', struct('material_id', 'glass', 'material_path', fullfile(repo_root, 'assets', 'materials', 'glass', 'glass.mat')), ...
        'west_wall', struct('material_id', 'wood', 'material_path', fullfile(repo_root, 'assets', 'materials', 'wood', 'Wood.mat')), ...
        'floor', struct('material_id', 'wood', 'material_path', fullfile(repo_root, 'assets', 'materials', 'wood', 'woodfloor.mat')), ...
        'ceiling', struct('material_id', 'plaster', 'material_path', fullfile(repo_root, 'assets', 'materials', 'plaster', 'mat_scene10_plaster.mat')));
end

function repo_root = get_repo_root()
    test_dir = fileparts(mfilename('fullpath'));
    repo_root = fileparts(fileparts(fileparts(test_dir)));
end

function verifyThrowsAny(testCase, func)
    did_throw = false;
    try
        func();
    catch
        did_throw = true;
    end

    verifyTrue(testCase, did_throw);
end

function wav_path = make_temp_wav(testCase)
    wav_path = [tempname, '.wav'];
    audiowrite(wav_path, zeros(32, 1), 8000);
    testCase.addTeardown(@() delete_if_exists(wav_path));
end

function delete_if_exists(path_value)
    if isfile(path_value)
        delete(path_value);
    end
end
