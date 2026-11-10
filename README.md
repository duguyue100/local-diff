# local-diff

Local, read-only, GitHub-style diff review. Trigger it from Neovim, pick a base
branch, and review the branch in your browser.

## Requirements

- Neovim 0.10+ (uses `vim.ui.open`)
- Python 3.12+ or [uv](https://docs.astral.sh/uv/)
- git

## Setup

Nothing to install: the server is one stdlib-only Python file. The Neovim plugin
runs it through `uv run --no-project`, which uses uv's shared cache instead of
creating a `.venv` in this repository (or in your project). If you prefer a local
environment, run `uv venv` here and the plugin will use `.venv/bin/python`.

## Use from Neovim

Add the repo to your plugin manager, for example with lazy.nvim:

```lua
{ dir = "~/path/to/local-diff" }
```

Then, from any directory inside a git repository:

```vim
:LocalDiff
```

The server starts on `0.0.0.0:8765` and the page opens at
`http://127.0.0.1:8765`. If that port is busy, the next free port is used.
`:LocalDiff /path/to/repo` opens another repository. `:LocalDiffStop` stops all
servers; `:LocalDiffStop /path/to/repo` stops one. Set `vim.g.local_diff_port`
before starting to prefer another port.

### Multiple projects and Neovim instances

Each repository gets its own server process, tracked by repository root. Running
`:LocalDiff` in project A and then in project B keeps both pages running; going
back to A reopens its existing page instead of starting another server. Multiple
Neovim instances work too: the second one takes the next free port.

### Server lifecycle

Servers are children of the Neovim instance that started them, not daemons. They
stop when you run `:LocalDiffStop`, when Neovim exits normally, and when Neovim
crashes: the server exits as soon as its stdin pipe closes. For standalone use,
`Ctrl+C` stops it.

### Network exposure

The default bind is `0.0.0.0`, so `http://<machine-ip>:8765` works from another
device on your network. There is no authentication: anyone who can reach the
port can read your diff. To keep it local, set:

```lua
vim.g.local_diff_host = "127.0.0.1"
```

or pass `--host 127.0.0.1` when running standalone.

## Use standalone

```sh
uv run --no-project local_diff.py --repo . --port 8765
# open http://127.0.0.1:8765
```

## What the diff shows

- Default base branch: `origin/HEAD`, then `main`, `master`, `develop`, then the
  first other local branch.
- Reviewing the checked-out branch compares its merge-base with the base branch
  to the working tree, so committed, staged, unstaged, and untracked changes are
  all visible.
- Reviewing a different branch uses the committed three-dot diff
  (`base...head`), matching a GitHub pull request.
- Nothing is cached. Refreshing the page after a commit re-runs git.

The page is split/unified toggleable. Your choice is kept in `localStorage`, and
the branch selection is kept in the URL so a refresh preserves it.

## Development

```sh
python3 -m unittest discover -s tests -v
```

No dependencies or build step.

The UI uses vendored [diff2html](https://github.com/rtfpessoa/diff2html)
(MIT) and [highlight.js](https://highlightjs.org/) (BSD-3-Clause). Their licenses
are in `static/vendor/`.

## Limits

Read-only: no comments, approvals, or GitHub API calls. Diff patches are capped
at 8 MB. Untracked files are diffed one by one; paths containing tabs or newlines
are not handled.
