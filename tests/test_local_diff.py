import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from local_diff import UserError, build_payload


class LocalDiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.repo / "app.txt").write_text("one\ntwo\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("checkout", "-b", "feature")
        (self.repo / "app.txt").write_text("one\nTWO\n")
        self.git("add", "app.txt")
        self.git("commit", "-m", "change app")
        (self.repo / "new file.txt").write_text("hello\n")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_default_compares_to_main_and_includes_working_tree(self):
        payload = build_payload(self.repo)

        self.assertEqual(payload["base"], "main")
        self.assertEqual(payload["head"], "feature")
        self.assertTrue(payload["workingTree"])
        self.assertIn("+TWO", payload["patch"])
        self.assertIn("new file.txt", payload["patch"])
        self.assertIn("+hello", payload["patch"])

    def test_committed_branch_diff_ignores_working_tree(self):
        self.git("checkout", "main")
        (self.repo / "app.txt").write_text("one\ntwo\nTHREE\n")

        payload = build_payload(self.repo, "main", "feature")

        self.assertFalse(payload["workingTree"])
        self.assertIn("+TWO", payload["patch"])
        self.assertNotIn("THREE", payload["patch"])

    def test_unknown_branch_is_rejected(self):
        with self.assertRaises(UserError):
            build_payload(self.repo, "does-not-exist", "feature")

    def test_server_exits_when_stdin_closes(self):
        script = Path(__file__).resolve().parents[1] / "local_diff.py"
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--exit-on-stdin-close",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            lines = queue.Queue()

            def read_stdout():
                for line in process.stdout:
                    lines.put(line.strip())

            threading.Thread(target=read_stdout, daemon=True).start()

            url = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=deadline - time.monotonic())
                except queue.Empty:
                    break
                if line.startswith("http://"):
                    url = line
                    break

            if url is None:
                process.kill()
                process.wait()
                self.fail(f"no URL printed; stderr={process.stderr.read()!r}")

            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
