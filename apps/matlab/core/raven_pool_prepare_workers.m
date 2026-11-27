function raven_pool_prepare_workers(pool, app_dir)
    % Make every pool worker ready to render before the server reports ready.
    %
    % Adds the MATLAB app to each worker's path, then performs the first
    % ITA-Toolbox preferences access one worker at a time. That first access
    % rewrites matlabprefs.mat in the session's preferences directory, which
    % all workers share; serializing it avoids one worker reading the file
    % while another replaces it.
    wait(parfevalOnAll(pool, @addpath, 0, genpath(app_dir)));
    spmd
        for turn = 1:spmdSize
            if spmdIndex == turn
                ita_preferences('AuthorStr');
            end
            spmdBarrier;
        end
    end
end
