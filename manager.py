import os
import time
import uuid
import shutil
import signal
import threading
import subprocess

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


# ============================================================
# CONFIG
# ============================================================

GITHUB_TOKEN = os.environ.get("GG_TOKEN")

GITHUB_SCOPE = os.environ.get(
    "GITHUB_SCOPE",
    "kingking0020/mmm",
)

TARGET_RUNNERS = int(
    os.environ.get("TARGET_RUNNERS", "15")
)

CHECK_INTERVAL = int(
    os.environ.get("CHECK_INTERVAL", "15")
)

# IMPORTANT:
# First test with ONE runner.
MAX_CREATING = int(
    os.environ.get("MAX_CREATING", "1")
)

FAILURE_BACKOFF = int(
    os.environ.get("FAILURE_BACKOFF", "30")
)

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo",
)

PORT = int(
    os.environ.get("PORT", "3000")
)

RUNNER_ARCHIVE = Path(
    os.environ.get(
        "RUNNER_ARCHIVE",
        "/opt/actions-runner.tar.gz",
    )
)

MANAGER_DIR = Path(
    os.environ.get(
        "RUNNER_MANAGER_DIR",
        "/tmp/runner-manager",
    )
)

RUNNERS_DIR = (
    MANAGER_DIR / "runners"
)


# ============================================================
# GITHUB API
# ============================================================

API_BASE = "https://api.github.com"

REPO_API = (
    f"{API_BASE}/repos/{GITHUB_SCOPE}"
)

RUNNERS_URL = (
    f"{REPO_API}/actions/runners"
)

REGISTRATION_TOKEN_URL = (
    f"{REPO_API}/actions/runners/registration-token"
)


# ============================================================
# STATE
# ============================================================

lock = threading.Lock()

running_runners = {}

creating_runners = 0

last_failure_time = 0

stop_event = threading.Event()


# ============================================================
# SIGNALS
# ============================================================

def shutdown(signum, frame):
    print()
    print("[MANAGER] Shutdown requested...")
    stop_event.set()


signal.signal(
    signal.SIGTERM,
    shutdown,
)

signal.signal(
    signal.SIGINT,
    shutdown,
)


# ============================================================
# VALIDATION
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


try:
    MANAGER_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    RUNNERS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

except Exception as e:
    print(
        f"[ERROR] Cannot create /tmp runtime: {e}"
    )
    raise SystemExit(1)


if not RUNNER_ARCHIVE.is_file():
    print(
        f"[ERROR] Runner archive missing: "
        f"{RUNNER_ARCHIVE}"
    )
    raise SystemExit(1)


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain",
        )

        self.end_headers()

        self.wfile.write(
            b"OK\n"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    try:

        server = ThreadingHTTPServer(
            (
                "0.0.0.0",
                PORT,
            ),
            HealthHandler,
        )

        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )

        thread.start()

        print(
            f"[HEALTH] Listening on {PORT}"
        )

        return server

    except Exception as e:

        print(
            f"[WARN] Health server error: {e}"
        )

        return None


# ============================================================
# GITHUB
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "Authorization":
            f"Bearer {GITHUB_TOKEN}",

        "Accept":
            "application/vnd.github+json",

        "X-GitHub-Api-Version":
            "2026-03-10",
    }
)


def get_online_runners():

    try:

        r = session.get(
            RUNNERS_URL,
            params={
                "per_page": 100,
            },
            timeout=20,
        )

        if r.status_code != 200:

            print(
                f"[ERROR] GitHub API: "
                f"{r.status_code}"
            )

            print(
                r.text[:500]
            )

            return None

        data = r.json()

        runners = data.get(
            "runners",
            []
        )

        online = [
            x for x in runners
            if x.get("status") == "online"
        ]

        busy = [
            x for x in online
            if x.get("busy") is True
        ]

        print(
            f"[GITHUB] "
            f"Registered={len(runners)} "
            f"Online={len(online)} "
            f"Busy={len(busy)}"
        )

        return len(online)

    except Exception as e:

        print(
            f"[ERROR] Runner list: {e}"
        )

        return None


def get_registration_token():

    try:

        r = session.post(
            REGISTRATION_TOKEN_URL,
            timeout=20,
        )

        if r.status_code not in (
            200,
            201,
        ):

            print(
                f"[ERROR] Token HTTP "
                f"{r.status_code}"
            )

            print(
                r.text[:500]
            )

            return None

        return r.json().get(
            "token"
        )

    except Exception as e:

        print(
            f"[ERROR] Token request: {e}"
        )

        return None


# ============================================================
# STORAGE
# ============================================================

def show_storage():

    try:

        u = shutil.disk_usage(
            MANAGER_DIR
        )

        print(
            "[STORAGE] "
            f"Used={u.used / 1024**3:.3f}GB "
            f"Free={u.free / 1024**3:.3f}GB "
            f"Total={u.total / 1024**3:.3f}GB"
        )

    except Exception as e:

        print(
            f"[STORAGE] {e}"
        )


# ============================================================
# CREATE REAL INSTALLATION
# ============================================================

def extract_runner(
    runner_dir: Path
):

    print(
        "[RUNNER] Extracting REAL runner "
        "installation..."
    )

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        result = subprocess.run(
            [
                "tar",
                "-xzf",
                str(RUNNER_ARCHIVE),
                "-C",
                str(runner_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )

        if result.returncode != 0:

            print(
                "[ERROR] tar extraction failed:"
            )

            print(
                result.stdout
            )

            return False

        # ----------------------------------------------------
        # Critical verification
        # ----------------------------------------------------

        listener_dll = (
            runner_dir /
            "bin" /
            "Runner.Listener.dll"
        )

        listener = (
            runner_dir /
            "bin" /
            "Runner.Listener"
        )

        config = (
            runner_dir /
            "config.sh"
        )

        run = (
            runner_dir /
            "run.sh"
        )

        print(
            "[RUNNER] Verification:"
        )

        print(
            f"        Root:     {runner_dir}"
        )

        print(
            f"        Config:   {config}"
        )

        print(
            f"        Listener: {listener}"
        )

        print(
            f"        DLL:      {listener_dll}"
        )

        if not config.is_file():
            print(
                "[ERROR] config.sh missing"
            )
            return False

        if not run.is_file():
            print(
                "[ERROR] run.sh missing"
            )
            return False

        if not listener.is_file():
            print(
                "[ERROR] Runner.Listener missing"
            )
            return False

        if not listener_dll.is_file():
            print(
                "[ERROR] Runner.Listener.dll missing"
            )
            return False

        # ----------------------------------------------------
        # Show REAL PATHS
        # ----------------------------------------------------

        print(
            "[RUNNER] REAL PATHS:"
        )

        print(
            subprocess.run(
                [
                    "realpath",
                    str(config),
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
        )

        print(
            subprocess.run(
                [
                    "realpath",
                    str(listener_dll),
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
        )

        return True

    except Exception as e:

        print(
            f"[ERROR] Extraction error: {e}"
        )

        return False


# ============================================================
# ENVIRONMENT
# ============================================================

def runner_env(
    runner_dir: Path
):

    home = (
        runner_dir /
        "_home"
    )

    temp = (
        runner_dir /
        "_temp"
    )

    tool_cache = (
        runner_dir /
        "_tool_cache"
    )

    home.mkdir(
        exist_ok=True
    )

    temp.mkdir(
        exist_ok=True
    )

    tool_cache.mkdir(
        exist_ok=True
    )

    env = os.environ.copy()

    env["HOME"] = str(home)

    env["TMPDIR"] = str(temp)

    env["TMP"] = str(temp)

    env["TEMP"] = str(temp)

    env["RUNNER_TEMP"] = str(temp)

    env["RUNNER_TOOL_CACHE"] = str(
        tool_cache
    )

    env["AGENT_TOOLSDIRECTORY"] = str(
        tool_cache
    )

    return env


# ============================================================
# CLEANUP
# ============================================================

def cleanup(
    name,
    path,
):

    with lock:

        running_runners.pop(
            name,
            None,
        )

    try:

        shutil.rmtree(
            path,
            ignore_errors=True,
        )

    except Exception:
        pass

    print(
        f"[RUNNER] {name} cleaned."
    )


# ============================================================
# CREATE RUNNER
# ============================================================

def create_runner():

    global creating_runners
    global last_failure_time

    name = (
        "deplexo-" +
        uuid.uuid4().hex[:12]
    )

    runner_dir = (
        RUNNERS_DIR /
        name
    )

    print()
    print(
        "=========================================="
    )

    print(
        "[RUNNER] Creating new runner"
    )

    print(
        f"[RUNNER] Name: {name}"
    )

    print(
        f"[RUNNER] Repo: {GITHUB_SCOPE}"
    )

    print(
        f"[RUNNER] Dir: {runner_dir}"
    )

    print(
        "=========================================="
    )

    try:

        token = (
            get_registration_token()
        )

        if not token:

            last_failure_time = time.time()

            return False

        if not extract_runner(
            runner_dir
        ):

            cleanup(
                name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        env = runner_env(
            runner_dir
        )

        config = (
            runner_dir /
            "config.sh"
        )

        # ----------------------------------------------------
        # Registration
        # ----------------------------------------------------

        print(
            f"[RUNNER] Registering {name}..."
        )

        result = subprocess.run(
            [
                "bash",
                str(config),

                "--url",
                f"https://github.com/{GITHUB_SCOPE}",

                "--token",
                token,

                "--name",
                name,

                "--labels",
                RUNNER_LABELS,

                "--work",
                "_work",

                "--ephemeral",

                "--disableupdate",

                "--unattended",
            ],
            cwd=runner_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )

        print(
            result.stdout or ""
        )

        if result.returncode != 0:

            print(
                f"[ERROR] Registration failed "
                f"with exit code "
                f"{result.returncode}"
            )

            cleanup(
                name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # VERIFY CONFIG
        # ----------------------------------------------------

        if not (
            runner_dir /
            ".runner"
        ).is_file():

            print(
                "[ERROR] .runner missing"
            )

            cleanup(
                name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        print(
            f"[RUNNER] {name} registered successfully."
        )

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        process = subprocess.Popen(
            [
                "bash",
                str(
                    runner_dir /
                    "run.sh"
                ),
            ],
            cwd=runner_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with lock:

            running_runners[
                name
            ] = {
                "process": process,
                "directory": runner_dir,
            }

        print(
            f"[RUNNER] {name} started."
        )

        # ----------------------------------------------------
        # MONITOR
        # ----------------------------------------------------

        def monitor():

            try:

                if process.stdout:

                    for line in process.stdout:

                        line = line.rstrip()

                        if line:

                            print(
                                f"[{name}] "
                                f"{line}"
                            )

                process.wait()

            except Exception as e:

                print(
                    f"[ERROR] Monitor "
                    f"{name}: {e}"
                )

            finally:

                print(
                    f"[RUNNER] "
                    f"{name} stopped."
                )

                cleanup(
                    name,
                    runner_dir,
                )

        threading.Thread(
            target=monitor,
            daemon=True,
        ).start()

        return True

    except Exception as e:

        print(
            f"[ERROR] create_runner: {e}"
        )

        cleanup(
            name,
            runner_dir,
        )

        last_failure_time = time.time()

        return False

    finally:

        with lock:

            creating_runners -= 1


# ============================================================
# START CREATION
# ============================================================

def start_creation():

    global creating_runners

    with lock:

        if (
            creating_runners >=
            MAX_CREATING
        ):
            return False

        creating_runners += 1

    threading.Thread(
        target=create_runner,
        daemon=True,
    ).start()

    return True


# ============================================================
# RECONCILE
# ============================================================

def reconcile():

    global last_failure_time

    online = (
        get_online_runners()
    )

    if online is None:
        return

    with lock:

        creating = (
            creating_runners
        )

    local = (
        len(running_runners)
    )

    print(
        f"[MANAGER] "
        f"GitHub online={online} "
        f"local={local} "
        f"creating={creating} "
        f"target={TARGET_RUNNERS}"
    )

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached."
        )

        return

    missing = (
        TARGET_RUNNERS -
        online
    )

    if last_failure_time:

        elapsed = (
            time.time() -
            last_failure_time
        )

        if elapsed < FAILURE_BACKOFF:

            print(
                f"[MANAGER] "
                f"Failure backoff: "
                f"{int(FAILURE_BACKOFF - elapsed)}s"
            )

            return

    effective_missing = (
        missing -
        creating
    )

    if effective_missing <= 0:
        return

    available = (
        MAX_CREATING -
        creating
    )

    count = min(
        effective_missing,
        available,
    )

    print(
        f"[MANAGER] Starting "
        f"{count} runner creation(s)."
    )

    for _ in range(count):

        if stop_event.is_set():
            return

        if not start_creation():
            return

        time.sleep(2)


# ============================================================
# STARTUP
# ============================================================

print()
print(
    "=========================================="
)

print(
    "      DEPLEXO GITHUB RUNNER MANAGER"
)

print(
    "=========================================="
)

print(
    "Manager repo : kingking200p0/sss"
)

print(
    f"Target repo  : {GITHUB_SCOPE}"
)

print(
    f"Target       : {TARGET_RUNNERS}"
)

print(
    f"Interval     : {CHECK_INTERVAL}s"
)

print(
    f"Max creating : {MAX_CREATING}"
)

print(
    f"Runtime      : {MANAGER_DIR}"
)

print(
    f"Archive      : {RUNNER_ARCHIVE}"
)

print(
    "=========================================="
)

print()


health_server = (
    start_health_server()
)

show_storage()

print(
    "[MANAGER] Initial GitHub check..."
)

get_online_runners()


# ============================================================
# MAIN LOOP
# ============================================================

while not stop_event.is_set():

    try:

        reconcile()

        show_storage()

    except Exception as e:

        print(
            f"[ERROR] Main loop: {e}"
        )

    stop_event.wait(
        CHECK_INTERVAL
    )


# ============================================================
# SHUTDOWN
# ============================================================

print(
    "[MANAGER] Stopping..."
)

with lock:

    processes = list(
        running_runners.values()
    )

for item in processes:

    process = item.get(
        "process"
    )

    if process:

        try:
            process.terminate()
        except Exception:
            pass


if health_server:

    try:
        health_server.shutdown()
    except Exception:
        pass


print(
    "[MANAGER] Shutdown complete."
)
