function tests = test_host_runtime
    tests = functiontests(localfunctions);
end

function setup(testCase)
    root = fullfile(tempdir, 'binascape-host-runtime', random_hex_id());
    mkdir(root);
    testCase.TestData.root = root;
    testCase.addTeardown(@() rmdir(root, 's'));
end

function testBackendIsAvailable(testCase)
    verifyTrue(testCase, any(strcmp(host_runtime_backend(), {'dotnet', 'java'})));
end

function testCanonicalPathNormalizesAndFlagsRelative(testCase)
    root = testCase.TestData.root;
    [canonical, is_absolute] = canonical_absolute_path(fullfile(root, 'a', '..', 'b.txt'));
    verifyTrue(testCase, is_absolute);
    verifyEqual(testCase, lower(strrep(canonical, '\', '/')), ...
        lower(strrep(fullfile(root, 'b.txt'), '\', '/')));
    [~, is_absolute] = canonical_absolute_path(fullfile('relativo', 'b.txt'));
    verifyFalse(testCase, is_absolute);
end

function testAtomicMoveRenamesAndRefusesExistingTarget(testCase)
    root = testCase.TestData.root;
    source = fullfile(root, 'origen.tmp');
    target = fullfile(root, 'destino á.json');
    write_text(source, 'uno');
    atomic_move_file(source, target);
    verifyFalse(testCase, isfile(source));
    verifyEqual(testCase, fileread(target), 'uno');
    write_text(source, 'dos');
    verifyError(testCase, @() atomic_move_file(source, target), ?MException);
    verifyEqual(testCase, fileread(target), 'uno');
    verifyTrue(testCase, isfile(source));
end

function write_text(path_value, text)
    fid = fopen(path_value, 'wb');
    fwrite(fid, uint8(text), 'uint8');
    fclose(fid);
end
