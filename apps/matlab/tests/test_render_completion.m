function tests = test_render_completion
    tests = functiontests(localfunctions);
end

function setup(testCase)
    root = fullfile(tempdir, 'opencode', char(java.util.UUID.randomUUID));
    mkdir(root);
    testCase.TestData.root = root;
    testCase.addTeardown(@() rmdir(root, 's'));
    manifest = fullfile(root, 'input_manifest.json');
    write_bytes(manifest, uint8('abc'));
    testCase.TestData.manifest = manifest;
    output = struct('output_type', 'binaural_hrtf', ...
        'wav_path', fullfile(root, 'audio á con espacios.wav'), ...
        'metadata_path', fullfile(root, 'audio á con espacios.json'));
    write_bytes(output.wav_path, uint8('abc'));
    write_bytes(output.metadata_path, uint8('{}'));
    testCase.TestData.cfg = struct('scene_id', 'scene', 'render', struct('outputs', output));
    testCase.TestData.context = struct('attempt_id', repmat('a', 1, 32), ...
        'variant_id', 'scene__binaural_hrtf', 'manifest_sha256', sha256_file(manifest), ...
        'completion_path', fullfile(root, 'completion.json'));
end

function testKnownSha256AndMultiBlockInput(testCase)
    verifyEqual(testCase, sha256_file(testCase.TestData.manifest), ...
        'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    large = fullfile(testCase.TestData.root, 'large.bin');
    write_bytes(large, repmat(uint8('a'), 1, 1000000));
    verifyEqual(testCase, sha256_file(large), ...
        'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0');
    % Más de un bloque de lectura; comparar con digest Java de una sola entrada.
    bytes = repmat(uint8(0:255), 1, 9000);
    write_bytes(large, bytes);
    digest = java.security.MessageDigest.getInstance('SHA-256');
    digest.update(typecast(bytes(:), 'int8'));
    expected = lower(reshape(dec2hex(typecast(digest.digest(), 'uint8'), 2).', 1, []));
    verifyEqual(testCase, sha256_file(large), expected);
end

function testSingleVariantReceiptHasExactFilesAndHashes(testCase)
    data = testCase.TestData;
    write_render_completion(data.manifest, data.cfg, data.context);
    receipt = jsondecode(fileread(data.context.completion_path));
    verifyEqual(testCase, receipt.schema_version, 1);
    verifyEqual(testCase, receipt.attempt_id, data.context.attempt_id);
    verifyEqual(testCase, receipt.variant_id, data.context.variant_id);
    verifyEqual(testCase, receipt.scene_id, 'scene');
    verifyEqual(testCase, receipt.output_type, 'binaural_hrtf');
    verifyEqual(testCase, receipt.manifest_sha256, data.context.manifest_sha256);
    verifyEqual(testCase, {receipt.artifacts.kind}, {'wav', 'metadata'});
    verifyEqual(testCase, [receipt.artifacts.size_bytes], [3 2]);
    for item = transpose(receipt.artifacts(:))
        verifyEqual(testCase, item.sha256, sha256_file(item.path));
        verifyTrue(testCase, isfile(item.path));
    end
    verifyTrue(testCase, contains(receipt.artifacts(1).path, 'á con espacios'));
    verifyFalse(testCase, isfile([data.context.completion_path '.tmp']));
end

function testMultiVariantInventoryAndOptionalContext(testCase)
    data = testCase.TestData;
    cfg = data.cfg;
    second = cfg.render.outputs;
    second.output_type = 'circular_array';
    second.wav_path = fullfile(data.root, 'second.wav');
    second.metadata_path = fullfile(data.root, 'second.json');
    write_bytes(second.wav_path, uint8('second'));
    write_bytes(second.metadata_path, uint8('{}'));
    cfg.render.outputs(2) = second;
    write_render_completion(data.manifest, cfg, []);
    verifyFalse(testCase, isfile(data.context.completion_path));
    write_render_completion(data.manifest, cfg, data.context);
    receipt = jsondecode(fileread(data.context.completion_path));
    verifyEqual(testCase, numel(receipt.artifacts), 4);
    verifyEqual(testCase, numel(unique({receipt.artifacts.path})), 4);
    verifyEqual(testCase, receipt.output_type, 'batch');
end

function testReadmeDocumentsTheOptionalContext(testCase)
    root = fileparts(fileparts(mfilename('fullpath')));
    text = fileread(fullfile(root, 'README.md'));
    fields = fieldnames(testCase.TestData.context);
    for i = 1:numel(fields)
        verifyTrue(testCase, contains(text, ['''' fields{i} '''']));
    end
    verifyTrue(testCase, contains(text, 'run_raven_static_render(snapshot_path, [], completion_context)'));
    verifyTrue(testCase, contains(text, 'El render'));
    verifyTrue(testCase, contains(text, 'dinámico continúa sin soporte'));
end

function testMissingArtifactAndManifestMismatchNeverPublish(testCase)
    data = testCase.TestData;
    context = data.context;
    context.manifest_sha256 = repmat('0', 1, 64);
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, context), ...
        'BinaScape:Completion:ManifestMismatch');
    verifyFalse(testCase, isfile(context.completion_path));
    delete(data.cfg.render.outputs.wav_path);
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), ...
        'BinaScape:Completion:MissingArtifact');
    verifyFalse(testCase, isfile(context.completion_path));
end

function testCannotReplacePriorCompletion(testCase)
    data = testCase.TestData;
    write_render_completion(data.manifest, data.cfg, data.context);
    before = fileread(data.context.completion_path);
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), ...
        'BinaScape:Completion:AlreadyExists');
    verifyEqual(testCase, fileread(data.context.completion_path), before);
end

function testTemporaryOpenAndPublishFailuresNeverComplete(testCase)
    data = testCase.TestData;
    temporary = [data.context.completion_path '.tmp'];
    mkdir(temporary); % Una ruta no escribible como archivo temporal.
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), ...
        'BinaScape:Completion:WriteFailed');
    verifyFalse(testCase, isfile(data.context.completion_path));
    rmdir(temporary);
    % Un directorio final preexistente tampoco se puede reemplazar.
    mkdir(data.context.completion_path);
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), ...
        'BinaScape:Completion:AlreadyExists');
    verifyTrue(testCase, isfolder(data.context.completion_path));
end

function testShortWritesAndCloseFailuresAreFatal(testCase)
    global COMPLETION_TEST_FAILURE
    data = testCase.TestData;
    install_io_stubs(testCase);
    for mode = ["short", "close"]
        COMPLETION_TEST_FAILURE = char(mode);
        identifier = 'BinaScape:Completion:WriteFailed';
        if mode == "close", identifier = 'BinaScape:Completion:CloseFailed'; end
        verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), identifier);
        verifyFalse(testCase, isfile(data.context.completion_path));
        verifyTrue(testCase, isfile([data.context.completion_path '.tmp']));
    end
end

function testMetadataShortWritesAndCloseFailuresAreFatal(testCase)
    global COMPLETION_TEST_FAILURE
    data = testCase.TestData;
    install_io_stubs(testCase);
    cfg = struct('schema_version', '3.0', 'scene_id', 'scene', 'job_id', 'job', 'scene_type', 'static');
    result = struct('fs', 44100, 'hrtf_id', 'test', 'summary', struct());
    target = fullfile(data.root, 'metadata-failure.json');
    for mode = ["short", "close"]
        COMPLETION_TEST_FAILURE = char(mode);
        identifier = 'BinaScape:Metadata:WriteFailed';
        if mode == "close", identifier = 'BinaScape:Metadata:CloseFailed'; end
        verifyError(testCase, @() export_render_metadata(cfg, result, target), identifier);
    end
end

function testOutputHashFailureNeverPublishes(testCase)
    data = testCase.TestData;
    write_stub(data.root, 'sha256_file', [
        "function value = sha256_file(path_value)"
        "if endsWith(path_value, '.wav'), error('Test:Hash', 'hash failed'); end"
        "value = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';"
        "end"]);
    previous = pwd;
    cd(data.root);
    clear sha256_file
    testCase.addTeardown(@() restore_hash(previous));
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), 'Test:Hash');
    verifyFalse(testCase, isfile(data.context.completion_path));
end

function testTargetAppearingBeforePublicationDoesNotGetReplaced(testCase)
    global COMPLETION_TEST_FAILURE
    data = testCase.TestData;
    install_io_stubs(testCase);
    COMPLETION_TEST_FAILURE = 'publish';
    verifyError(testCase, @() write_render_completion(data.manifest, data.cfg, data.context), ...
        'BinaScape:Completion:AlreadyExists');
    verifyTrue(testCase, isfolder(data.context.completion_path));
    verifyTrue(testCase, isfile([data.context.completion_path '.tmp']));
end

function restore_hash(previous)
    cd(previous);
    clear sha256_file
end

function install_io_stubs(testCase)
    root = testCase.TestData.root;
    write_stub(root, 'fwrite', [
        "function count = fwrite(fid, data, varargin)"
        "global COMPLETION_TEST_FAILURE"
        "name = fopen(fid);"
        "selected = endsWith(name, 'completion.json.tmp') || endsWith(name, 'metadata-failure.json');"
        "if selected && strcmp(COMPLETION_TEST_FAILURE, 'short'), data=data(1:end-1); end"
        "count = builtin('fwrite', fid, data, varargin{:});"
        "end"]);
    write_stub(root, 'fclose', [
        "function status = fclose(fid)"
        "global COMPLETION_TEST_FAILURE"
        "name = fopen(fid);"
        "selected = endsWith(name, 'completion.json.tmp') || endsWith(name, 'metadata-failure.json');"
        "status = builtin('fclose', fid);"
        "if selected && strcmp(COMPLETION_TEST_FAILURE, 'close'), status=-1; end"
        "if endsWith(name, 'completion.json.tmp') && strcmp(COMPLETION_TEST_FAILURE, 'publish'), mkdir(name(1:end-4)); end"
        "end"]);
    previous = pwd;
    cd(root);
    clear fwrite fclose
    testCase.addTeardown(@() restore_io(previous));
end

function restore_io(previous)
    cd(previous);
    clear fwrite fclose
    clear global COMPLETION_TEST_FAILURE
end

function write_stub(root, name, lines)
    fid = fopen(fullfile(root, [name '.m']), 'w');
    assert(fid ~= -1);
    fprintf(fid, '%s\n', lines);
    assert(fclose(fid) == 0);
end

function write_bytes(path_value, bytes)
    fid = fopen(path_value, 'wb');
    assert(fid ~= -1);
    assert(fwrite(fid, bytes, 'uint8') == numel(bytes));
    assert(fclose(fid) == 0);
end
