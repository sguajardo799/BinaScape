function tests = test_background_noise_helpers
    tests = functiontests(localfunctions);
end

function testColoredNoiseDeterministicAndRms(testCase)
    colors = {'white', 'pink', 'brown', 'blue', 'violet'};
    for i = 1:numel(colors)
        a = generate_colored_noise(colors{i}, 2048, 123);
        b = generate_colored_noise(colors{i}, 2048, 123);

        verifyEqual(testCase, size(a), [2048 1]);
        verifyEqual(testCase, a, b, 'AbsTol', 1e-12);
        verifyEqual(testCase, sqrt(mean(a .^ 2)), 1, 'AbsTol', 1e-10);
    end
end

function testGenerateColoredNoiseRestoresRng(testCase)
    rng(99, 'twister');
    expected_state = rng();

    generate_colored_noise('white', 32, 5);
    actual_state = rng();

    verifyEqual(testCase, actual_state.Seed, expected_state.Seed);
    verifyEqual(testCase, actual_state.Type, expected_state.Type);
    verifyEqual(testCase, actual_state.State, expected_state.State);
end

function testFitNoiseLoopsTrimsAndAdaptsChannels(testCase)
    [fit, info] = fit_noise_to_audio_shape((1:3).', 8, 2);

    verifyEqual(testCase, size(fit), [8 2]);
    verifyEqual(testCase, fit(:, 1), [1; 2; 3; 1; 2; 3; 1; 2]);
    verifyEqual(testCase, fit(:, 1), fit(:, 2));
    verifyEqual(testCase, info.duration_mode, 'loop');
    verifyEqual(testCase, info.channel_mode, 'mono_replicated');

    [trimmed, info] = fit_noise_to_audio_shape([(1:5).' (11:15).'], 3, 2);
    verifyEqual(testCase, trimmed, [1 11; 2 12; 3 13]);
    verifyEqual(testCase, info.duration_mode, 'trim');
    verifyEqual(testCase, info.channel_mode, 'channel_to_channel');
end

function testFitNoiseRejectsIncompatibleChannels(testCase)
    verifyThrowsAny(testCase, @() fit_noise_to_audio_shape(randn(8, 3), 8, 2));
end

function testScaleNoiseToSnr(testCase)
    signal = ones(1000, 2);
    noise = ones(1000, 2);

    [scaled, info] = scale_noise_to_snr(signal, noise, 20);

    verifyEqual(testCase, sqrt(mean(scaled(:) .^ 2)), 0.1, 'AbsTol', 1e-12);
    verifyFalse(testCase, info.skipped);
    verifyEqual(testCase, info.gain, 0.1, 'AbsTol', 1e-12);
end

function testScaleNoiseSkipsSilentSignal(testCase)
    [scaled, info] = scale_noise_to_snr(zeros(16, 2), ones(16, 2), 10);

    verifyEqual(testCase, scaled, zeros(16, 2));
    verifyTrue(testCase, info.skipped);
    verifyEqual(testCase, info.skipped_reason, 'silent_signal');
end

function testPrepareNoiseLayerAudioFile(testCase)
    wav_path = [tempname, '.wav'];
    audiowrite(wav_path, (1:4).' / 10, 8000);
    testCase.addTeardown(@() delete_if_exists(wav_path));
    layer = struct('strategy', 'audio_file', 'strategy_original', 'audio_folder', ...
        'path', wav_path, 'snr_db', 10);

    [noise, info] = prepare_noise_layer(layer, 8000, 10, 2, 4, 1);

    verifyEqual(testCase, size(noise), [10 2]);
    verifyEqual(testCase, noise(:, 1), noise(:, 2));
    verifyEqual(testCase, info.duration_mode, 'loop');
    verifyEqual(testCase, info.channel_mode, 'mono_replicated');
    verifyFalse(testCase, info.resampled);
    verifyEqual(testCase, info.strategy_original, 'audio_folder');
end

function testPrepareNoiseLayerResamplesWhenNeeded(testCase)
    wav_path = [tempname, '.wav'];
    audiowrite(wav_path, sin(2 * pi * (0:99).' / 20), 4000);
    testCase.addTeardown(@() delete_if_exists(wav_path));
    layer = struct('strategy', 'audio_file', 'strategy_original', 'audio_file', ...
        'path', wav_path, 'snr_db', 10);

    [noise, info] = prepare_noise_layer(layer, 8000, 200, 1, [], 1);

    verifyEqual(testCase, size(noise), [200 1]);
    verifyTrue(testCase, info.resampled);
    verifyEqual(testCase, info.source_fs, 4000);
end

function testApplyBackgroundNoiseNoOpAndGuard(testCase)
    audio = 0.1 * ones(1000, 2);
    cfg = struct('enabled', false, 'layers', struct([]));

    [out, meta] = apply_background_noise(audio, 8000, cfg, struct('effective_seed', 1));
    verifyEqual(testCase, out, audio);
    verifyFalse(testCase, meta.applied);

    cfg = struct('enabled', true, 'layers', struct('strategy', 'colored', ...
        'strategy_original', 'colored', 'color', 'white', 'snr_db', -40));
    [out, meta] = apply_background_noise(audio, 8000, cfg, struct('effective_seed', 1));

    verifyLessThanOrEqual(testCase, max(abs(out), [], 'all'), 0.999 + 1e-12);
    verifyTrue(testCase, meta.applied);
    verifyTrue(testCase, meta.guard_applied);
    verifyEqual(testCase, numel(meta.layers), 1);
end

function testApplyBackgroundNoiseMultipleLayers(testCase)
    audio = 0.5 * ones(1000, 2);
    layers = [ ...
        struct('strategy', 'colored', 'strategy_original', 'colored', 'color', 'white', 'snr_db', 30), ...
        struct('strategy', 'colored', 'strategy_original', 'colored', 'color', 'pink', 'snr_db', 30) ...
    ];
    cfg = struct('enabled', true, 'layers', layers);

    [out, meta] = apply_background_noise(audio, 8000, cfg, struct('effective_seed', 10));

    verifyNotEqual(testCase, out, audio);
    verifyTrue(testCase, meta.applied);
    verifyEqual(testCase, numel(meta.layers), 2);
    verifyTrue(testCase, all([meta.layers.applied]));
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

function delete_if_exists(path_value)
    if isfile(path_value)
        delete(path_value);
    end
end
