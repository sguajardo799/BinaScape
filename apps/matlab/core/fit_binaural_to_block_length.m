function y = fit_binaural_to_block_length(y, block_len)
    if size(y, 1) > block_len
        y = y(1:block_len, :);
    elseif size(y, 1) < block_len
        pad = zeros(block_len - size(y, 1), size(y, 2));
        y = [y; pad];
    end
end