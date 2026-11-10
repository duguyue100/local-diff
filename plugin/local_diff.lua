local root = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h:h")

-- repo root -> { job = <id>, url = <string|nil> }
local servers = {}

local function notify(message, level)
  vim.notify("local-diff: " .. message, level or vim.log.levels.INFO)
end

local function open_url(target)
  if vim.ui.open then
    vim.ui.open(target)
    return
  end
  vim.fn.jobstart({ vim.fn.has("mac") == 1 and "open" or "xdg-open", target }, { detach = true })
end

local function repo_root(input)
  local path = input ~= "" and vim.fn.fnamemodify(vim.fn.expand(input), ":p") or vim.fn.getcwd()
  local found = vim.fs.root(path, ".git")
  return vim.fn.resolve(vim.fs.normalize(found or path))
end

local function command(repo, host, port)
  local venv = root .. "/.venv/bin/python"
  local script = root .. "/local_diff.py"
  local cmd
  if vim.fn.executable(venv) == 1 then
    cmd = { venv, script }
  elseif vim.fn.executable("uv") == 1 then
    cmd = { "uv", "run", "--quiet", "--no-project", script }
  elseif vim.fn.executable("python3") == 1 then
    cmd = { "python3", script }
  else
    return nil
  end
  vim.list_extend(cmd, { "--repo", repo, "--host", host, "--port", tostring(port), "--exit-on-stdin-close" })
  return cmd
end

local function start(opts)
  local repo = repo_root(opts.args)
  local running = servers[repo]
  if running then
    if running.url then
      open_url(running.url)
    else
      notify("server is still starting for " .. repo)
    end
    return
  end

  local host = vim.g.local_diff_host or "0.0.0.0"
  local port = vim.g.local_diff_port or 8765
  local cmd = command(repo, host, port)
  if not cmd then
    notify("needs Python 3 or uv", vim.log.levels.ERROR)
    return
  end

  local entry = {}
  local opened = false
  local saw_error = false
  servers[repo] = entry
  entry.job = vim.fn.jobstart(cmd, {
    cwd = repo,
    on_stdout = function(_, data)
      entry.stdout = (entry.stdout or "") .. table.concat(data or {}, "\n")
      while true do
        local line, rest = entry.stdout:match("^([^\n]*)\n(.*)$")
        if not line then
          break
        end
        entry.stdout = rest
        local found = vim.trim(line):match("^http://[%w%.%-%:]+:%d+$")
        if found and not opened then
          opened = true
          entry.url = found
          vim.schedule(function()
            open_url(found)
          end)
        end
      end
    end,
    on_stderr = function(_, data)
      local message = table.concat(data or {}, "\n"):gsub("%s+$", "")
      if message ~= "" then
        saw_error = true
        vim.schedule(function()
          notify(message, vim.log.levels.ERROR)
        end)
      end
    end,
    on_exit = function(id, code)
      vim.schedule(function()
        if servers[repo] ~= entry or entry.job ~= id then
          return
        end
        servers[repo] = nil
        if code ~= 0 and not saw_error and vim.v.exiting == 0 then
          notify("server for " .. repo .. " exited with code " .. code, vim.log.levels.ERROR)
        end
      end)
    end,
  })

  if entry.job <= 0 then
    servers[repo] = nil
    notify("failed to start the server for " .. repo, vim.log.levels.ERROR)
  end
end

local function stop(opts)
  if opts.args ~= "" then
    local repo = repo_root(opts.args)
    local entry = servers[repo]
    if not entry then
      notify("no server for " .. repo)
      return
    end
    vim.fn.jobstop(entry.job)
    servers[repo] = nil
    return
  end

  local count = 0
  for repo, entry in pairs(servers) do
    vim.fn.jobstop(entry.job)
    servers[repo] = nil
    count = count + 1
  end
  notify(count > 0 and ("stopped " .. count .. " server(s)") or "no servers running")
end

vim.api.nvim_create_user_command("LocalDiff", start, {
  nargs = "?",
  complete = "dir",
  desc = "Open a local GitHub-style diff review page",
})
vim.api.nvim_create_user_command("LocalDiffStop", stop, {
  nargs = "?",
  complete = "dir",
  desc = "Stop local-diff server(s)",
})
