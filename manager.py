import os
import stat
import shutil
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


PORT = int(os.environ.get("PORT", "3000"))

PATHS = [
    Path("/opt/runner-source"),
    Path("/tmp"),
    Path("/tmp/runner-manager"),
    Path("/home/runner"),
]


def test_path(path: Path):
    print()
    print("=" * 60)
    print(f"[CHECK] {path}")
    print("=" * 60)

    print(f"exists : {path.exists()}")
    print(f"is_dir : {path.is_dir()}")

    if not path.exists():
        return

    try:
        mode = path.stat().st_mode

        print(
            "mode   : "
            + stat.filemode(mode)
        )
    except Exception as e:
        print(f"stat   : {e}")

    # write test
    test_file = path / ".deplexo_write_test"

    try:
        test_file.write_text("test")
        test_file.unlink()
        print("write  : OK")
    except Exception as e:
        print(f"write  : FAIL ({e})")

    # exec test
    if path.is_dir():

        script = path / ".deplexo_exec_test.sh"

        try:
            script.write_text(
                "#!/bin/sh\n"
                "printf 'EXEC_OK\\n'\n"
            )

            script.chmod(0o755)

            result = subprocess.run(
                [str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
            )

            print(
                f"exec   : "
                f"code={result.returncode} "
                f"output={result.stdout.strip()}"
            )

        except Exception as e:
            print(f"exec   : FAIL ({e})")

        finally:
            try:
                script.unlink()
            except Exception:
                pass


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain"
        )
        self.end_headers()
        self.wfile.write(
            b"Deplexo filesystem diagnostic OK\n"
        )

    def log_message(self, *args):
        pass


def health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler,
    )

    threading.Thread(
        target=server.serve_forever,
        daemon=True,
    ).start()

    print(
        f"[HEALTH] Listening on {PORT}"
    )


print()
print("=" * 60)
print(" DEPLEXO RUNNER FILESYSTEM DIAGNOSTIC")
print("=" * 60)
print()

for path in PATHS:
    test_path(path)

print()
print("=" * 60)
print("[RUNNER] Testing official Runner.Listener")
print("=" * 60)

listener = Path(
    "/opt/runner-source/bin/Runner.Listener"
)

print(
    f"path: {listener}"
)

print(
    f"exists: {listener.exists()}"
)

print(
    f"executable: {os.access(listener, os.X_OK)}"
)

try:

    result = subprocess.run(
        [str(listener), "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )

    print(
        f"exit: {result.returncode}"
    )

    print(
        result.stdout[:1000]
    )

except Exception as e:

    print(
        f"Runner.Listener test failed: {e}"
    )


health_server()

while True:
    try:
        pass
    except KeyboardInterrupt:
        break
