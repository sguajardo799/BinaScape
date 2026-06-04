function tests = test_static_output_naming
    tests = functiontests(localfunctions);
end

function testMultiHrtfDerivesUniqueOutputs(testCase)
    cfg = make_cfg([ ...
        struct('hrtf_id', 'Subject A', 'hrtf_path', 'a.daff'), ...
        struct('hrtf_id', 'Subject A', 'hrtf_path', 'b.daff') ...
    ]);

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.outputs(1).project_name, 'project__subject-a');
    verifyEqual(testCase, normalized.render.outputs(2).project_name, 'project__subject-a-2');
    verifyEqual(testCase, normalized.render.outputs(1).metadata_path, 'meta__subject-a.json');
    verifyEqual(testCase, normalized.render.outputs(2).wav_path, 'out__subject-a-2.wav');
end

function testSingleHrtfKeepsLegacyNames(testCase)
    cfg = make_cfg(struct('hrtf_id', 'Solo', 'hrtf_path', 'solo.daff'));
    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.outputs(1).project_name, 'project');
    verifyEqual(testCase, normalized.render.outputs(1).wav_path, 'out.wav');
    verifyEqual(testCase, normalized.render.outputs(1).metadata_path, 'meta.json');
end

function testSingleHrtfDerivesFileNamesFromDirectoryOutputs(testCase)
    cfg = make_cfg(struct('hrtf_id', 'Solo', 'hrtf_path', 'solo.daff'));
    cfg.scene_id = 'scene_static_0001';
    cfg.render.output_wav_path = '../../data/raven_rendered/';
    cfg.render.output_metadata_path = '../../data/raven_rendered/metadata/';

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.outputs(1).wav_path, ...
        fullfile('../../data/raven_rendered/', 'scene_static_0001.wav'));
    verifyEqual(testCase, normalized.render.outputs(1).metadata_path, ...
        fullfile('../../data/raven_rendered/metadata/', 'scene_static_0001.json'));
end

function testMultiHrtfDerivesFileNamesFromDirectoryOutputs(testCase)
    cfg = make_cfg([ ...
        struct('hrtf_id', 'Subject A', 'hrtf_path', 'a.daff'), ...
        struct('hrtf_id', 'Subject B', 'hrtf_path', 'b.daff') ...
    ]);
    cfg.scene_id = 'scene_static_0001';
    cfg.render.output_wav_path = '../../data/raven_rendered/';
    cfg.render.output_metadata_path = '../../data/raven_rendered/metadata/';

    normalized = validate_static_scene_config(cfg);

    verifyEqual(testCase, normalized.render.outputs(1).wav_path, ...
        fullfile('../../data/raven_rendered/', 'scene_static_0001__subject-a.wav'));
    verifyEqual(testCase, normalized.render.outputs(2).metadata_path, ...
        fullfile('../../data/raven_rendered/metadata/', 'scene_static_0001__subject-b.json'));
end

function cfg = make_cfg(hrtfs)
    repo_root = get_repo_root();
    cfg = struct();
    cfg.schema_version = '1.0';
    cfg.scene_type = 'static';
    cfg.scene_id = 'scene';
    cfg.job_id = 'job';
    cfg.base_rpf_file = 'base.rpf';
    cfg.project_name = 'project';
    cfg.room = make_room_cfg(repo_root);
    cfg.receiver = struct();
    cfg.receiver.position_m = [1.0 1.0 1.0];
    cfg.receiver.orientation_deg = struct('yaw', 0.0, 'pitch', 0.0, 'roll', 0.0);
    cfg.receiver.hrtfs = hrtfs;
    cfg.sources = struct('source_id', 'src1', 'audio_path', 'a.wav', 'position_m', [0 0 0], ...
        'orientation_deg', struct('yaw', 0.0, 'pitch', 0.0, 'roll', 0.0));
    cfg.render = struct('sample_rate_hz', 44100, 'output_wav_path', 'out.wav', ...
        'output_metadata_path', 'meta.json', 'seed', 123);
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
