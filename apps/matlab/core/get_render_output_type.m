function output_type = get_render_output_type(result)
    if isfield(result, 'output') && isfield(result.output, 'output_type') && ...
            ~isempty(result.output.output_type)
        output_type = char(string(result.output.output_type));
    elseif isfield(result, 'hrtf_id') && ~isempty(result.hrtf_id)
        output_type = char(string(result.hrtf_id));
    else
        error('BinaScape:Matlab:MissingOutputIdentity', 'La variante no define output_type.');
    end
end
