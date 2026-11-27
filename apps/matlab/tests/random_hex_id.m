function value = random_hex_id()
    % Identificador hexadecimal de 32 caracteres para tests, sin depender de
    % java.util.UUID (MATLAB R2026b ya no incluye JVM).
    value = lower(reshape(dec2hex(randi([0 255], 1, 16), 2).', 1, []));
end
