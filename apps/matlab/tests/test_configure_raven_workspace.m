function tests = test_configure_raven_workspace
    tests = functiontests(localfunctions);
end

function setup(testCase)
    testCase.TestData.root = tempname;
end

function teardown(testCase)
    if isfolder(testCase.TestData.root)
        rmdir(testCase.TestData.root, 's');
    end
end

function testCreatesPrivateMaterialDatabaseAndPointsRavenToIt(testCase)
    rpf = MockRavenProject();

    workspace = configure_raven_workspace(rpf, 'exp__scene_0001__binaural_hrtf', testCase.TestData.root);

    expected_root = fullfile(testCase.TestData.root, 'exp__scene_0001__binaural_hrtf');
    verifyEqual(testCase, workspace.root, expected_root);
    verifyEqual(testCase, workspace.material_database_path, fullfile(expected_root, 'MaterialDatabase'));
    verifyEqual(testCase, workspace.models_path, fullfile(expected_root, 'RavenModels'));
    verifyTrue(testCase, isfolder(workspace.material_database_path));
    verifyTrue(testCase, isfolder(workspace.models_path));
    verifyEqual(testCase, rpf.pathMaterials, workspace.material_database_path);
    verifyEqual(testCase, rpf.call_log, {'setPathMaterials'});
end

function testDifferentProjectsGetDifferentWorkspaces(testCase)
    first = configure_raven_workspace(MockRavenProject(), 'project_a', testCase.TestData.root);
    second = configure_raven_workspace(MockRavenProject(), 'project_b', testCase.TestData.root);

    verifyNotEqual(testCase, first.material_database_path, second.material_database_path);
    verifyNotEqual(testCase, first.models_path, second.models_path);
end

function testStaleWorkspaceContentIsRemoved(testCase)
    stale_dir = fullfile(testCase.TestData.root, 'project_a', 'MaterialDatabase');
    mkdir(stale_dir);
    fid = fopen(fullfile(stale_dir, 'wall_001.mat'), 'w');
    fclose(fid);

    workspace = configure_raven_workspace(MockRavenProject(), 'project_a', testCase.TestData.root);

    verifyEmpty(testCase, dir(fullfile(workspace.material_database_path, '*.mat')));
end

function testMaterialsAreWrittenOnlyAfterTheDatabaseIsPrivate(testCase)
    rpf = MockRavenProject();
    configure_raven_workspace(rpf, 'project_a', testCase.TestData.root);
    rpf.setMaterial('wall_001', 0.1, 0.1);

    verifyEqual(testCase, rpf.call_log, {'setPathMaterials', 'setMaterial'});
end

function testCleanupRemovesWorkspace(testCase)
    workspace = configure_raven_workspace(MockRavenProject(), 'project_a', testCase.TestData.root);

    cleanup_raven_workspace(workspace);

    verifyFalse(testCase, isfolder(workspace.root));
end

function testCleanupFailureOnlyWarns(testCase)
    workspace = struct('root', fullfile(testCase.TestData.root, 'not_a_folder.txt'));
    mkdir(testCase.TestData.root);
    fid = fopen(workspace.root, 'w');
    fclose(fid);

    verifyWarning(testCase, @() cleanup_raven_workspace(workspace), ...
        'BinaScape:Matlab:RavenWorkspaceCleanupFailed');
end

function testCleanupIgnoresMissingWorkspace(testCase)
    verifyWarningFree(testCase, @() cleanup_raven_workspace(struct()));
    verifyWarningFree(testCase, @() cleanup_raven_workspace( ...
        struct('root', fullfile(testCase.TestData.root, 'missing'))));
end

function testWorkspaceSummaryNamesTheUsedPaths(testCase)
    workspace = configure_raven_workspace(MockRavenProject(), 'project_a', testCase.TestData.root);
    ctx = struct('workspace', workspace, ...
        'raven_model_path', fullfile(workspace.models_path, 'room_6_faces'));

    summary = build_raven_workspace_summary(ctx);

    verifyEqual(testCase, summary.material_database_path, workspace.material_database_path);
    verifyEqual(testCase, summary.model_path, fullfile(workspace.models_path, 'room_6_faces'));
end

function testWorkspaceSummaryWithoutGeneratedModel(testCase)
    workspace = configure_raven_workspace(MockRavenProject(), 'project_a', testCase.TestData.root);

    summary = build_raven_workspace_summary(struct('workspace', workspace));

    verifyEqual(testCase, summary.material_database_path, workspace.material_database_path);
    verifyEqual(testCase, summary.model_path, '');
end
