function binascape_whis_run(task_list_path, install_root)
% BINASCAPE_WHIS_RUN  Process BinaScape WHIS channel tasks in one MATLAB session.
%
%   binascape_whis_run(task_list_path, install_root)
%
%   task_list_path: text file with one task JSON path per line. Each task describes one
%                   ear (channel) of one degradation job and lives next to its input WAV:
%       input_wav          float WAV at 48 kHz, mono, in the render digital domain (never normalized)
%       output_wav         path to write the processed mono WAV (float32)
%       result_json        path to write the result on success
%       error_json         path to write the error on failure
%       srcsnd_spl_db      SPL of this channel: K + 20*log10(rms) (design D2)
%       calib_tone_spl_db  K - 26: SPL of the 1 kHz tone at -26 dBFS RMS
%       hearing_level_db   7 thresholds at 125..8000 Hz (dB HL, >= 0)
%       compression_health 7 values in [0, 1]
%       synth_method       'DTVF' | 'FBAnaSyn'
%       out_mid_crct       'FreeField' | 'DiffuseField' | 'ITU' | 'EarDrum'
%   install_root:   directory containing WHIS/WHISv302 and gammachirp-filterbank/GCFBv234.
%
%   WHISv302 is called directly (not WHISv30_Batch) so each channel is analysed at its own
%   calibrated SPL and the synthesis output stays in the input digital domain. A failing task
%   writes error_json and the session continues with the next one.

whis_dir = fullfile(install_root, 'WHIS', 'WHISv302');
gcfb_dir = fullfile(install_root, 'gammachirp-filterbank', 'GCFBv234');
if ~isfolder(whis_dir) || ~isfolder(gcfb_dir)
    error('binascape:whis:install', 'install_root must contain WHIS/WHISv302 and gammachirp-filterbank/GCFBv234: %s', install_root);
end
addpath(whis_dir);
StartupWHIS;

tasks = strtrim(splitlines(fileread(task_list_path)));
tasks = tasks(~cellfun(@isempty, tasks));
matlab_release = version('-release');

for n = 1:numel(tasks)
    task = jsondecode(fileread(tasks{n}));
    try
        [snd, fs] = audioread(task.input_wav);
        if fs ~= 48000
            error('binascape:whis:fs', 'WHIS task input must be 48000 Hz, got %d Hz', fs);
        end
        P = struct();
        P.fs = 48000;
        P.SwGUIbatch = 'Batch';
        P.AllowDownSampling = 0;
        P.SynthMethod = task.synth_method;
        P.HLoss.Type = 'HL0';
        P.HLoss.HearingLeveldB = reshape(task.hearing_level_db, 1, []);
        P.HLoss.CompressionHealth = reshape(task.compression_health, 1, []);
        P.CalibTone.SPLdB = task.calib_tone_spl_db;
        P.SrcSnd.SPLdB = task.srcsnd_spl_db;
        P.GCparam.OutMidCrct = task.out_mid_crct;

        [out, P] = WHISv302(reshape(snd, 1, []), P);

        write_float_wav(task.output_wav, out(:), 48000);
        result = struct();
        result.whis_version = P.version;
        result.matlab_release = matlab_release;
        result.compression_health_requested = reshape(task.compression_health, 1, []);
        result.compression_health_applied = reshape(P.HLoss.CompressionHealth, 1, []);
        result.pin_loss_db_act = reshape(P.HLoss.PinLossdB_ACT, 1, []);
        result.pin_loss_db_pas = reshape(P.HLoss.PinLossdB_PAS, 1, []);
        result.out_mid_crct = P.GCparam.OutMidCrct;
        result.synth_method = P.SynthMethod;
        write_json(task.result_json, result);
    catch err
        failure = struct('identifier', err.identifier, 'message', err.message, ...
                         'matlab_release', matlab_release);
        if ~isempty(err.stack)
            failure.where = sprintf('%s:%d', err.stack(1).name, err.stack(1).line);
        end
        write_json(task.error_json, failure);
    end
end
end


function write_float_wav(path, samples, fs)
tmp = [path '.part.wav'];
audiowrite(tmp, samples, fs, 'BitsPerSample', 32);
movefile(tmp, path, 'f');
end


function write_json(path, value)
% Results are written to a temporary file and renamed so Python never reads a partial JSON.
tmp = [path '.part'];
fid = fopen(tmp, 'w');
fwrite(fid, jsonencode(value), 'char');
fclose(fid);
movefile(tmp, path, 'f');
end
