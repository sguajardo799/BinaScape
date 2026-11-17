function run_raven_pool_server(session_dir, pool_size, parent_pid, options)
    % Serve static render jobs from one MATLAB session on a local parallel pool.
    %
    % One `matlab -batch` process (one license checkout) opens a pool and renders
    % the attempts the orchestrator publishes as files:
    %   <session_dir>/requests/<attempt_id>.json   written by Python (atomic rename)
    %   <session_dir>/results/<attempt_id>.json    written here (atomic rename)
    %   <session_dir>/ready.json                   pool open and workers prepared
    %   <session_dir>/heartbeat.json               rewritten while the loop runs
    %   <session_dir>/stop                         written by Python to shut down
    %
    % Each job runs `render_fn(snapshot_path, diagnostics, completion_context)`,
    % `run_raven_static_render` by default. Its console (future Diary) is written
    % to the request's `console_path`. The server exits on `stop`, or when
    % `parent_pid` (> 0) no longer exists.
    %
    % options (optional struct, used by tests):
    %   render_fn        function handle, default @run_raven_static_render
    %   pool_factory     @(pool_size) -> pool, default parpool('Processes', n)
    %   prepare_workers  @(pool) -> [], default raven_pool_prepare_workers
    %   poll_s           loop period in seconds, default 0.5
    %   heartbeat_s      heartbeat period in seconds, default 2

    app_dir = fileparts(mfilename('fullpath'));
    addpath(genpath(app_dir));
    if nargin < 3 || isempty(parent_pid)
        parent_pid = 0;
    end
    if nargin < 4 || isempty(options)
        options = struct();
    end
    render_fn = option(options, 'render_fn', @run_raven_static_render);
    pool_factory = option(options, 'pool_factory', @(n) parpool('Processes', n));
    prepare_workers = option(options, 'prepare_workers', @(p) raven_pool_prepare_workers(p, app_dir));
    poll_s = option(options, 'poll_s', 0.5);
    heartbeat_s = option(options, 'heartbeat_s', 2);

    requests_dir = fullfile(session_dir, 'requests');
    results_dir = fullfile(session_dir, 'results');
    ensure_dir(requests_dir);
    ensure_dir(results_dir);

    fprintf('[POOL] opening pool with %d worker(s)\n', pool_size);
    pool = pool_factory(pool_size);
    cleanup_pool = onCleanup(@() delete_pool(pool));
    prepare_workers(pool);
    write_json_atomic(fullfile(session_dir, 'ready.json'), struct( ...
        'schema_version', 1, 'pool_size', pool.NumWorkers, 'pid', current_pid(), ...
        'ready_at', utc_now()));
    fprintf('[POOL] ready with %d worker(s)\n', pool.NumWorkers);

    running = containers.Map('KeyType', 'char', 'ValueType', 'any');
    seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
    last_heartbeat = -inf;
    while true
        if isfile(fullfile(session_dir, 'stop'))
            fprintf('[POOL] stop requested\n');
            break;
        end
        if parent_pid > 0 && ~process_alive(parent_pid)
            fprintf('[POOL] parent process %d is gone; shutting down\n', parent_pid);
            break;
        end

        submit_new_requests(requests_dir, results_dir, pool, render_fn, running, seen);
        collect_finished(results_dir, running);

        if toc_s() - last_heartbeat >= heartbeat_s
            write_json_atomic(fullfile(session_dir, 'heartbeat.json'), struct( ...
                'schema_version', 1, 'pid', current_pid(), 'at', utc_now(), ...
                'in_flight', running.Count));
            last_heartbeat = toc_s();
        end
        pause(poll_s);
    end

    keys = running.keys();
    for i = 1:numel(keys)
        cancel(running(keys{i}).future);
    end
    clear cleanup_pool;
    fprintf('[POOL] stopped\n');
end


function submit_new_requests(requests_dir, results_dir, pool, render_fn, running, seen)
    listing = dir(fullfile(requests_dir, '*.json'));
    for i = 1:numel(listing)
        [~, attempt_id] = fileparts(listing(i).name);
        if isKey(seen, attempt_id)
            continue;
        end
        seen(attempt_id) = true;
        job = struct('attempt_id', attempt_id, 'console_path', '', 'started_at', utc_now(), ...
            'future', []);
        try
            request = jsondecode(fileread(fullfile(requests_dir, listing(i).name)));
            job.console_path = request.console_path;
            job.future = parfeval(pool, @raven_pool_job, 1, render_fn, request.snapshot_path, ...
                as_struct_or_empty(request.diagnostics), as_struct_or_empty(request.completion_context));
            running(attempt_id) = job;
            fprintf('[POOL] job %s submitted\n', attempt_id);
        catch submit_error
            % A request that cannot be read or submitted ends as a failed job.
            write_result(results_dir, job, 1, submit_error, '');
        end
    end
end


function collect_finished(results_dir, running)
    keys = running.keys();
    for i = 1:numel(keys)
        job = running(keys{i});
        if ~strcmp(job.future.State, 'finished')
            continue;
        end
        write_console(job.console_path, job.future.Diary);
        worker_id = '';
        if isempty(job.future.Error)
            worker_id = fetchOutputs(job.future);
            write_result(results_dir, job, 0, [], worker_id);
        else
            write_result(results_dir, job, 1, job.future.Error, worker_id);
        end
        remove(running, keys{i});
        fprintf('[POOL] job %s finished (exit %d)\n', job.attempt_id, ~isempty(job.future.Error));
    end
end


function write_result(results_dir, job, exit_code, err, worker_id)
    error_info = [];
    if ~isempty(err)
        error_info = struct('identifier', err.identifier, 'message', err.message);
    end
    write_json_atomic(fullfile(results_dir, [job.attempt_id '.json']), struct( ...
        'schema_version', 1, 'attempt_id', job.attempt_id, 'exit_code', exit_code, ...
        'error', error_info, 'worker_id', worker_id, 'started_at', job.started_at, ...
        'finished_at', utc_now()));
end


function write_console(path, text)
    if isempty(path)
        return;
    end
    try
        fid = fopen(path, 'a', 'n', 'UTF-8');
        if fid < 0
            return;
        end
        closer = onCleanup(@() fclose(fid));
        fwrite(fid, text, 'char');
    catch
        % Console capture is diagnostic only; it never changes the job result.
    end
end


function write_json_atomic(path, value)
    temporary = [path '.tmp'];
    fid = fopen(temporary, 'w', 'n', 'UTF-8');
    if fid < 0
        error('BinaScape:Pool:WriteFailed', 'Cannot write %s', temporary);
    end
    fwrite(fid, jsonencode(value), 'char');
    fclose(fid);
    [ok, message] = movefile(temporary, path, 'f');
    if ~ok
        error('BinaScape:Pool:WriteFailed', 'Cannot publish %s: %s', path, message);
    end
end


function value = as_struct_or_empty(value)
    if ~isstruct(value)
        value = [];
    end
end


function value = option(options, name, default)
    if isfield(options, name) && ~isempty(options.(name))
        value = options.(name);
    else
        value = default;
    end
end


function ensure_dir(path)
    if ~isfolder(path)
        mkdir(path);
    end
end


function delete_pool(pool)
    try
        delete(pool);
    catch
    end
end


function alive = process_alive(pid)
    alive = true;
    if ~ispc
        return;
    end
    try
        System.Diagnostics.Process.GetProcessById(pid);
    catch
        alive = false;
    end
end


function pid = current_pid()
    pid = feature('getpid');
end


function value = utc_now()
    value = char(datetime('now', 'TimeZone', 'UTC', 'Format', 'yyyy-MM-dd''T''HH:mm:ss.SSSSSS''+00:00'''));
end


function seconds = toc_s()
    seconds = posixtime(datetime('now'));
end
