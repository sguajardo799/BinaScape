function tests = test_parse_room_material_file
    tests = functiontests(localfunctions);
end

function testParsesExplicitRoomMaterialFile(testCase)
    material = parse_room_material_file(get_material_path('bricks', 'Bricks.mat'));

    verifyEqual(testCase, material.name, 'Bricks');
    verifyEqual(testCase, numel(material.absorp), 31);
    verifyEqual(testCase, numel(material.scatter), 31);
    verifyEqual(testCase, material.absorp(1), 0.02, 'AbsTol', 1e-12);
    verifyEqual(testCase, material.scatter(31), 0.5399, 'AbsTol', 1e-12);
end

function testRejectsMissingMaterialFile(testCase)
    verifyThrowsAny(testCase, @() parse_room_material_file('D:\\missing\\material.mat'));
end

function material_path = get_material_path(folder_name, file_name)
    test_dir = fileparts(mfilename('fullpath'));
    repo_root = fileparts(fileparts(fileparts(test_dir)));
    material_path = fullfile(repo_root, 'assets', 'materials', folder_name, file_name);
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
