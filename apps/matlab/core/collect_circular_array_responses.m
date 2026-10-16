function responses = collect_circular_array_responses(rpf, n_sources, n_microphones, fs)
    % Matriz RAVEN [fuente,micrófono] -> un itaAudio N-canales por fuente.
    raw = rpf.getImpulseResponseItaAudio();
    if ~isequal(size(raw), [n_sources n_microphones])
        error('BinaScape:Matlab:ArrayResponseShape', 'RIR debe tener tamaño [fuentes,micrófonos].');
    end
    responses = itaAudio(1, n_sources);
    for s = 1:n_sources
        lengths = zeros(1, n_microphones);
        for k = 1:n_microphones
            response = raw(s,k);
            if response.samplingRate ~= fs || response.nChannels ~= 1 || ...
                    isempty(response.timeData) || any(~isfinite(response.timeData), 'all')
                error('BinaScape:Matlab:ArrayResponseShape', 'RIR mono a %d Hz requerido por par.', fs);
            end
            lengths(k) = response.nSamples;
        end
        data = zeros(max(lengths), n_microphones);
        for k = 1:n_microphones
            data(1:lengths(k),k) = raw(s,k).timeData;
        end
        responses(s).timeData = data;
        responses(s).samplingRate = fs;
        responses(s).signalType = 'energy';
        for k = 1:n_microphones
            responses(s).channelNames{k} = raw(s,k).channelNames{1};
        end
    end
end
