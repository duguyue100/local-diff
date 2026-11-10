#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""Local, read-only, GitHub-style diff review server."""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = (ROOT / "static").resolve()
MAX_PATCH_BYTES = 8 * 1024 * 1024
DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"
LOCAL_HOST_NAMES = {"localhost", socket.gethostname(), socket.getfqdn()}


class UserError(Exception):
    """A bad request that should be shown to the user."""


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_ok(repo: Path, *args: str) -> str:
    result = git(repo, *args)
    if result.returncode:
        raise UserError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def find_repo(path: str | Path) -> Path:
    result = git(Path(path).resolve(), "rev-parse", "--show-toplevel")
    if result.returncode or not result.stdout.strip():
        raise UserError(f"Not a git repository: {path}")
    return Path(result.stdout.strip())


def repo_name(repo: Path) -> str:
    result = git(repo, "remote", "get-url", "origin")
    if result.returncode == 0 and result.stdout.strip():
        remote = result.stdout.strip().removesuffix(".git").rstrip("/")
        if remote.startswith("git@"):
            remote = remote.split(":", 1)[-1]
        parts = remote.split("/")
        if len(parts) >= 2:
            return "/".join(parts[-2:])
    return repo.name


def list_branches(repo: Path) -> list[str]:
    return [b for b in git_ok(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines() if b]


def current_branch(repo: Path) -> str | None:
    result = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


def default_base(repo: Path, branches: list[str], current: str | None) -> str:
    remote_head = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    remote_name = remote_head.stdout.strip().split("/", 1)[-1] if remote_head.returncode == 0 else None
    for name in (remote_name, "main", "master", "develop"):
        if name and name in branches and name != current:
            return name
    for name in branches:
        if name != current:
            return name
    return current or "HEAD"


def is_commit(repo: Path, name: str) -> bool:
    result = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}")
    return result.returncode == 0


def untracked_patch(repo: Path) -> str:
    """git diff ignores untracked files, so make a /dev/null patch for each one."""
    names = git_ok(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    chunks = []
    for name in filter(None, names):
        result = git(repo, "diff", "--no-color", "--no-ext-diff", "--no-index", "--", "/dev/null", name)
        if result.returncode in (0, 1):
            chunks.append(result.stdout)
    return "".join(chunks)


def make_patch(repo: Path, base: str, head: str) -> tuple[str, bool]:
    if not is_commit(repo, base):
        raise UserError(f"Unknown branch or commit: {base}")
    if not is_commit(repo, head):
        raise UserError(f"Unknown branch or commit: {head}")

    current = current_branch(repo)
    working_tree = head == current or (current is None and head == "HEAD")
    if working_tree:
        merge_base = git(repo, "merge-base", base, "HEAD")
        start = merge_base.stdout.strip() if merge_base.returncode == 0 else base
        patch = git_ok(repo, "diff", "--no-color", "--no-ext-diff", "--find-renames", "-U3", start, "--")
        patch += untracked_patch(repo)
    else:
        patch = git_ok(
            repo,
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--find-renames",
            "-U3",
            f"{base}...{head}",
            "--",
        )

    if len(patch.encode("utf-8", "replace")) > MAX_PATCH_BYTES:
        raise UserError(f"Diff is larger than {MAX_PATCH_BYTES // (1024 * 1024)} MB")
    return patch, working_tree


def build_payload(repo: Path, base: str | None = None, head: str | None = None) -> dict:
    branches = list_branches(repo)
    current = current_branch(repo)
    base = base or default_base(repo, branches, current)
    head = head or current or "HEAD"
    patch, working_tree = make_patch(repo, base, head)
    return {
        "repo": str(repo),
        "name": repo_name(repo),
        "branches": branches,
        "current": current,
        "base": base,
        "head": head,
        "workingTree": working_tree,
        "patch": patch,
    }


class Handler(BaseHTTPRequestHandler):
    repo: Path

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json({"error": "Invalid Host header"}, 403)
            return

        url = urlparse(self.path)
        if url.path == "/api/diff":
            query = parse_qs(url.query)
            base = (query.get("base") or [None])[0] or None
            head = (query.get("head") or [None])[0] or None
            try:
                payload = build_payload(self.repo, base, head)
            except UserError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json(payload)
            return

        if url.path == "/":
            self._file(STATIC_DIR / "index.html")
            return

        if url.path.startswith("/static/"):
            requested = (STATIC_DIR / unquote(url.path.removeprefix("/static/"))).resolve()
            if not requested.is_relative_to(STATIC_DIR):
                self._json({"error": "Not found"}, 404)
                return
            self._file(requested)
            return

        self._json({"error": "Not found"}, 404)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return True
        try:
            name = urlparse(f"//{host}").hostname
            if name in LOCAL_HOST_NAMES:
                return True
            # Accept IP literals (LAN access) but not arbitrary DNS names, which
            # would let a DNS-rebinding page read the diff.
            ipaddress.ip_address(name)
            return True
        except ValueError:
            return False

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json({"error": "Not found"}, 404)
            return
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
            mime += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        pass


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    """Try the requested port, then the next ones, so parallel projects/nvims work."""
    last_error = None
    for candidate in range(port, min(port + 20, 65536)):
        try:
            return ThreadingHTTPServer((host, candidate), Handler)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc
    raise last_error or OSError(f"no free port near {port}")


def exit_when_stdin_closes(server: ThreadingHTTPServer) -> None:
    """Neovim keeps the child's stdin pipe open; EOF means the editor is gone."""

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(1):
                pass
        except (OSError, ValueError):
            pass
        server.shutdown()

    threading.Thread(target=watch, daemon=True).start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve a local, GitHub-style diff review page.")
    parser.add_argument("--repo", default=".", help="any path inside the repository (default: current directory)")
    parser.add_argument(
        "--host",
        default=os.environ.get("LOCAL_DIFF_HOST", DEFAULT_HOST),
        help=f"interface to bind (default: {DEFAULT_HOST}; use 127.0.0.1 to stay local)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LOCAL_DIFF_PORT", DEFAULT_PORT)),
        help=f"preferred port; the next free one is used if busy (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--exit-on-stdin-close",
        action="store_true",
        help="exit when stdin closes (used by the Neovim plugin)",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    try:
        repo = find_repo(args.repo)
    except UserError as exc:
        parser.exit(1, f"local-diff: {exc}\n")

    Handler.repo = repo
    try:
        server = create_server(args.host, args.port)
    except OSError as exc:
        parser.exit(1, f"local-diff: cannot listen on {args.host}:{args.port}: {exc}\n")

    port = server.server_address[1]
    local_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    print(f"local-diff: {repo}", flush=True)
    print(f"http://{local_host}:{port}", flush=True)
    if args.exit_on_stdin_close:
        exit_when_stdin_closes(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
