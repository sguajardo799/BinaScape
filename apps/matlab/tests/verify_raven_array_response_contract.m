function report = verify_raven_array_response_contract(base_rpf_path, output_dir)
    % Verificación de integración: requiere ITA Toolbox y RAVEN reales.
    % No modifica el RPF base. Conserva resultados en output_dir.
    % Uso: verify_raven_array_response_contract(base_rpf_path, output_dir)
    if ~isfile(base_rpf_path)
        error('BinaScape:Matlab:MissingBaseRPF', 'RPF base inexistente: %s', base_rpf_path);
    end
    if ~isfolder(output_dir)
        mkdir(output_dir);
    end
    rpf = itaRavenProject(base_rpf_path);
    rpf.copyProjectToNewRPFFile(fullfile(output_dir, 'array_contract.rpf'));
    rpf.setProjectName('BinaScape_ArrayContract');
    rpf.setOutputPath(output_dir);
    rpf.keepImpulseResponseFiles(true);

    % Coordenadas públicas: Y vertical. Orden de canales hacia +Z.
    center = [2.1, 1.4, 1.8];
    radius = 0.2;
    microphones = center + radius * [1 0 0; 0 0 1; -1 0 0; 0 0 -1];
    sources = [3.3 1.4 1.2; 1.0 1.4 2.7];
    reflection = [1 1 -1];
    rpf.setSourceNames({'contract_source_0', 'contract_source_1'});
    rpf.setSourcePositions(sources .* reflection);
    rpf.setSourceViewVectors(repmat([1 0 0], 2, 1));
    rpf.setSourceUpVectors(repmat([0 1 0], 2, 1));
    rpf.setSourceDirectivity('');
    rpf.setSourceSoundStates([1 1]);
    rpf.setSourceLevels([100 100]);
    names = {'contract_mic_0', 'contract_mic_1', 'contract_mic_2', 'contract_mic_3'};
    rpf.setReceiverNames(names);
    rpf.setReceiverPositions(microphones .* reflection);
    rpf.setReceiverViewVectors(repmat([1 0 0], 4, 1));
    rpf.setReceiverUpVectors(repmat([0 1 0], 4, 1));
    rpf.setReceiverStates(ones(1, 4));
    rpf.setGenerateRIR(1);
    rpf.setGenerateBRIR(0);
    rpf.setSimulationTypeIS(1);
    rpf.setSimulationTypeRT(1);
    rpf.setNumParticles(60000);
    rpf.setISOrder_PS(2);
    rpf.setFixReflectionPattern(1);
    rpf.setFixPoissonSequence(1);
    rpf.run();

    responses = rpf.getImpulseResponseItaAudio();
    assert(isequal(size(responses), [2 4]), ...
        'Se esperaba una matriz itaAudio [fuente, micrófono] de tamaño [2 4].');
    arrival_samples = zeros(2, 4);
    direct_peaks = zeros(2, 4);
    distances = zeros(2, 4);
    for s = 1:2
        for k = 1:4
            response = responses(s, k);
            assert(response.samplingRate == 44100, 'La respuesta no usa 44100 Hz.');
            assert(response.nChannels == 1, 'Cada par fuente/micrófono debe ser mono.');
            assert(strcmp(response.channelNames{1}, names{k}), 'Orden de micrófonos incorrecto.');
            data = response.timeData;
            assert(all(isfinite(data), 'all') && any(data ~= 0), 'RIR vacío o no finito.');
            distances(s, k) = norm(sources(s, :) - microphones(k, :));
            expected = distances(s, k) / 343 * 44100;
            % El filtro interpolado puede tener pre-ringing; medir el pico
            % de sonido directo en una ventana anterior a las reflexiones.
            first = max(1, floor(expected) - 40);
            last = min(size(data, 1), ceil(expected) + 40);
            [direct_peaks(s, k), peak_index] = max(abs(data(first:last, 1)));
            arrival_samples(s, k) = first + peak_index - 2;
            assert(abs(arrival_samples(s, k) - expected) < 20, ...
                'El tiempo de llegada no corresponde a la distancia del par fuente/micrófono.');
        end
        [~, closest] = min(distances(s, :));
        [~, farthest] = max(distances(s, :));
        assert(arrival_samples(s, closest) < arrival_samples(s, farthest), ...
            'Se perdió el retardo relativo entre micrófonos.');
        assert(direct_peaks(s, closest) > direct_peaks(s, farthest), ...
            'Se perdió la diferencia de nivel del sonido directo.');
    end
    raw_t30 = rpf.getT30();
    collected = collect_circular_array_responses(rpf, 2, 4, 44100);
    for s = 1:2
        for k = 1:4
            assert(isequal(collected(s).timeData(:,k), responses(s,k).timeData), ...
                'La adaptación alteró el orden, tiempo o nivel del RIR.');
        end
    end
    averaged_t30 = rpf.getT30(true, false);
    assert(isnumeric(averaged_t30) && numel(averaged_t30) == 10 && ...
        all(isfinite(averaged_t30)) && all(averaged_t30 > 0), ...
        'T30 promedio no contiene diez bandas finitas positivas.');

    report = struct('api', 'getImpulseResponseItaAudio', ...
        'response_shape', size(responses), 'dimension_order', 'source,microphone', ...
        'sample_rate_hz', 44100, 'source_positions_m', sources, ...
        'microphone_positions_m', microphones, 'distances_m', distances, ...
        'arrival_samples', arrival_samples, 'direct_peaks', direct_peaks, ...
        'raw_t30_class', class(raw_t30), 'raw_t30_shape', size(raw_t30), ...
        'averaged_t30_s', averaged_t30);
    report_path = fullfile(output_dir, 'array_contract_report.json');
    fid = fopen(report_path, 'w');
    assert(fid >= 0, 'No se pudo abrir el informe de verificación.');
    cleanup = onCleanup(@() fclose(fid));
    fwrite(fid, jsonencode(report, 'PrettyPrint', true), 'char');
    disp(report);
end
