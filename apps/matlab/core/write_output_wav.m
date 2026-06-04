function write_output_wav(audio, fs, output_path)
    out_dir = fileparts(output_path);
    if ~isempty(out_dir) && ~isfolder(out_dir)
        mkdir(out_dir);
    end

    audiowrite(output_path, audio, fs);
end