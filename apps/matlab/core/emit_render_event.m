function emit_render_event(diagnostics, event_type, stage, details)
    % Evidencia de mejor esfuerzo; nunca modifica el resultado del renderer.
    persistent sequences
    fid = -1;
    try
        if nargin < 1 || isempty(diagnostics)
            return;
        end
        if nargin < 4
            details = struct();
        end
        if isempty(sequences)
            sequences = containers.Map('KeyType', 'char', 'ValueType', 'double');
        end
        attempt_id = char(diagnostics.attempt_id);
        if ~isKey(sequences, attempt_id)
            sequences(attempt_id) = 0;
        end
        sequences(attempt_id) = sequences(attempt_id) + 1;
        event = details;
        event.schema_version = 1;
        event.attempt_id = attempt_id;
        event.sequence = sequences(attempt_id);
        event.timestamp = char(datetime('now', 'TimeZone', 'UTC', ...
            'Format', "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"));
        event.type = event_type;
        event.stage = stage;
        text = jsonencode(event);
        fid = fopen(diagnostics.events_path, 'a', 'n', 'UTF-8');
        if fid == -1
            error('BinaScape:Diagnostics:OpenFailed', 'No se pudo abrir el archivo de eventos.');
        end
        if fprintf(fid, '%s\n', text) < 0
            error('BinaScape:Diagnostics:WriteFailed', 'No se pudo escribir el evento.');
        end
        status = fclose(fid);
        fid = -1;
        if status ~= 0
            error('BinaScape:Diagnostics:CloseFailed', 'No se pudo cerrar el archivo de eventos.');
        end
    catch ME
        if fid ~= -1
            try
                fclose(fid);
            catch
            end
        end
        try
            warning('BinaScape:Diagnostics:Unavailable', 'Diagnóstico incompleto: %s', ME.message);
        catch
            % Una política warning-as-error o consola rota tampoco debe propagarse.
            try
                fprintf(2, '[WARN] Diagnóstico incompleto: %s\n', ME.message);
            catch
            end
        end
    end
end
