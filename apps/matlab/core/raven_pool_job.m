function worker_id = raven_pool_job(render_fn, snapshot_path, diagnostics, completion_context)
    % Run one render job on a pool worker and report which worker ran it.
    %
    % Errors from render_fn propagate unchanged, so the pool server publishes a
    % non-zero exit code exactly where `matlab -batch` would have exited non-zero.
    worker_id = sprintf('process-%d', feature('getpid'));
    try
        worker = getCurrentWorker();
        if ~isempty(worker) && isprop(worker, 'ProcessId')
            worker_id = sprintf('worker-%d', worker.ProcessId);
        end
    catch
        % Thread-based pools have no process worker; keep the process id.
    end
    render_fn(snapshot_path, diagnostics, completion_context);
end
