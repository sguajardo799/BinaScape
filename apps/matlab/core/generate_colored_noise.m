function noise = generate_colored_noise(color, n_samples, seed)
    if nargin < 3
        seed = [];
    end

    color = lower(strtrim(char(string(color))));
    if ~isscalar(n_samples) || ~isnumeric(n_samples) || ~isfinite(n_samples) || n_samples < 0
        error('n_samples must be a non-negative numeric scalar.');
    end
    n_samples = round(double(n_samples));

    if n_samples == 0
        noise = zeros(0, 1);
        return;
    end

    switch color
        case 'white'
            alpha = 0;
        case 'pink'
            alpha = -1;
        case 'brown'
            alpha = -2;
        case 'blue'
            alpha = 1;
        case 'violet'
            alpha = 2;
        otherwise
            error('Unsupported colored noise color: %s', color);
    end

    previous_rng = rng();
    cleanup = onCleanup(@() rng(previous_rng));
    if ~isempty(seed)
        rng(double(seed), 'twister');
    end

    white = randn(n_samples, 1);
    if alpha == 0
        noise = white;
    else
        spectrum = fft(white);
        freqs = (0:n_samples-1).';
        mirrored = min(freqs, n_samples - freqs);
        weights = mirrored .^ (alpha / 2);
        weights(1) = 0;
        weights(~isfinite(weights)) = 0;
        noise = real(ifft(spectrum .* weights));
    end

    noise = noise - mean(noise);
    rms_value = sqrt(mean(noise(:) .^ 2));
    if rms_value > 0
        noise = noise ./ rms_value;
    end
end
