import os
import time
import uuid
import shutil
import signal
import socket
import threading
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


# ============================================================
# CONFIG
# ============================================================

GITHUB_TOKEN = os.environ.get("GG_TOKEN")

# TARGET REPOSITORY ONLY
# The manager itself is NOT deployed here.
GITHUB_SCOPE = os.environ.get(
    "GITHUB_SCOPE",
    "kingking0020/mmm",
)

TARGET_RUNNERS = int(
    os.environ.get(
        "TARGET_RUNNERS",
        "15",
    )
)

CHECK_INTERVAL = int(
    os.environ.get(
        "CHECK_INTERVAL",
        "20",
    )
)

MAX_CREATING = int(
    os.environ.get(
        "MAX_CREATING",
        "3",
    )
)

FAILURE_BACKOFF = int(
    os.environ.get(
        "FAILURE_BACKOFF",
        "30",
    )
)

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo",
)

PORT = int(
    os.environ.get(
        "PORT",
        "8080",
    )
)


# ============================================================
# DIRECTORIES
# ============================================================

# Deplexo runtime filesystem is read-only outside /tmp.
MANAGER_DIR = Path(
    os.environ.get(
        "RUNNER_MANAGER_DIR",
        "/tmp/runner-manager",
    )
)

RUNNERS_DIR = (
    MANAGER_DIR /
    "runners"
)

SOURCE_DIR = Path(
    "/opt/runner-source"
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
# GLOBAL STATE
# ============================================================

state_lock = threading.Lock()

running_runners = {}

creating_runners = 0

last_failure_time = 0

stop_event = threading.Event()


# ============================================================
# SIGNAL HANDLERS
# ============================================================

def shutdown_handler(signum, frame):
    print()
    print("[MANAGER] Shutdown requested...")
    stop_event.set()


signal.signal(
    signal.SIGTERM,
    shutdown_handler,
)

signal.signal(
    signal.SIGINT,
    shutdown_handler,
)


# ============================================================
# VALIDATION
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


# ============================================================
# PREPARE WRITABLE DIRECTORY
# ============================================================

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
        "[ERROR] Could not create writable runtime directory:"
    )

    print(
        f"        {e}"
    )

    print(
        f"[ERROR] Runtime path: {MANAGER_DIR}"
    )

    raise SystemExit(1)


# ============================================================
# HTTP HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"OK\n"
        )

    def log_message(
        self,
        format,
        *args,
    ):
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
            f"[HEALTH] HTTP health server listening on "
            f"0.0.0.0:{PORT}"
        )

        return server

    except Exception as e:

        print(
            f"[WARN] Health server could not start: {e}"
        )

        return None


# ============================================================
# GITHUB SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "Authorization": (
            f"Bearer {GITHUB_TOKEN}"
        ),
        "Accept": (
            "application/vnd.github+json"
        ),
        "X-GitHub-Api-Version": (
            "2026-03-10"
        ),
    }
)


# ============================================================
# GITHUB RUNNER LIST
# ============================================================

def get_online_runners():

    try:

        response = session.get(
            RUNNERS_URL,
            params={
                "per_page": 100,
            },
            timeout=20,
        )

        if response.status_code != 200:

            print(
                "[ERROR] Failed to list GitHub runners:"
            )

            print(
                f"        HTTP {response.status_code}"
            )

            print(
                f"        {response.text[:500]}"
            )

            return None

        data = response.json()

        runners = data.get(
            "runners",
            [],
        )

        online = [
            runner
            for runner in runners
            if runner.get("status") == "online"
        ]

        busy = [
            runner
            for runner in online
            if runner.get("busy") is True
        ]

        print(
            f"[GITHUB] Registered: {len(runners)} | "
            f"Online: {len(online)} | "
            f"Busy: {len(busy)}"
        )

        return len(online)

    except Exception as e:

        print(
            f"[ERROR] get_online_runners(): {e}"
        )

        return None


# ============================================================
# GET REGISTRATION TOKEN
# ============================================================

def get_registration_token():

    try:

        response = session.post(
            REGISTRATION_TOKEN_URL,
            timeout=20,
        )

        if response.status_code not in (
            200,
            201,
        ):

            print(
                "[ERROR] Registration token request failed:"
            )

            print(
                f"        HTTP {response.status_code}"
            )

            print(
                f"        {response.text[:500]}"
            )

            return None

        data = response.json()

        token = data.get(
            "token"
        )

        if not token:

            print(
                "[ERROR] GitHub returned no registration token."
            )

            return None

        return token

    except Exception as e:

        print(
            f"[ERROR] get_registration_token(): {e}"
        )

        return None


# ============================================================
# STORAGE
# ============================================================

def show_storage():

    try:

        usage = shutil.disk_usage(
            MANAGER_DIR
        )

        total_gb = (
            usage.total /
            1024 /
            1024 /
            1024
        )

        free_gb = (
            usage.free /
            1024 /
            1024 /
            1024
        )

        used_gb = (
            usage.used /
            1024 /
            1024 /
            1024
        )

        print(
            f"[STORAGE] "
            f"Used={used_gb:.2f}GB | "
            f"Free={free_gb:.2f}GB | "
            f"Total={total_gb:.2f}GB"
        )

    except Exception as e:

        print(
            f"[STORAGE] Error: {e}"
        )


# ============================================================
# SOURCE VALIDATION
# ============================================================

def validate_source():

    required = [
        "config.sh",
        "run.sh",
        "env.sh",
        "bin",
    ]

    print(
        "[MANAGER] Checking runner source..."
    )

    if not SOURCE_DIR.exists():

        print(
            f"[ERROR] Runner source does not exist: "
            f"{SOURCE_DIR}"
        )

        return False

    for name in required:

        path = (
            SOURCE_DIR /
            name
        )

        if not path.exists():

            print(
                f"[ERROR] Missing runner source item: "
                f"{path}"
            )

            return False

    print(
        "[MANAGER] Runner source is valid."
    )

    return True


# ============================================================
# CREATE LIGHTWEIGHT RUNNER TREE
# ============================================================

def create_runner_tree(
    runner_dir: Path,
):
    """
    Build a per-runner filesystem without copying the huge
    GitHub Actions Runner package 15 times.

    Regular files:
        hard-linked

    Large static directories:
        symlinked to read-only source

    Writable directories/files:
        created separately for each runner
    """

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        # ----------------------------------------------------
        # Link every top-level item from source.
        # ----------------------------------------------------

        for source_item in SOURCE_DIR.iterdir():

            destination = (
                runner_dir /
                source_item.name
            )

            # Never share mutable runtime directories.
            if source_item.name in {
                "_work",
                "_diag",
                "_temp",
                "_tool_cache",
                "home",
            }:
                continue

            # ------------------------------------------------
            # Directories:
            # use symlink, zero-copy
            # ------------------------------------------------

            if source_item.is_dir():

                os.symlink(
                    source_item,
                    destination,
                    target_is_directory=True,
                )

            # ------------------------------------------------
            # Regular files:
            # use hard-link
            #
            # Important:
            # hard-link makes the script's own path appear
            # as the runner-specific path instead of a symlink.
            # ------------------------------------------------

            elif source_item.is_file():

                os.link(
                    source_item,
                    destination,
                )

            else:

                # Preserve uncommon filesystem entries as
                # symlinks when possible.
                os.symlink(
                    source_item,
                    destination,
                )

        # ----------------------------------------------------
        # Remove shared mutable files if they exist.
        #
        # Normally they don't exist in the source image,
        # but this protects us if the image contains them.
        # ----------------------------------------------------

        mutable_files = [
            ".env",
            ".path",
            ".runner",
            ".credentials",
            ".credentials_rsaparams",
            ".credentials_rsakey",
        ]

        for name in mutable_files:

            path = (
                runner_dir /
                name
            )

            try:

                if path.exists() or path.is_symlink():
                    path.unlink()

            except Exception:
                pass

        # ----------------------------------------------------
        # Unique writable directories
        # ----------------------------------------------------

        writable_dirs = [
            "_work",
            "_diag",
            "_temp",
            "_tool_cache",
            "home",
        ]

        for name in writable_dirs:

            (
                runner_dir /
                name
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        # ----------------------------------------------------
        # Writable HOME
        # ----------------------------------------------------

        home_dir = (
            runner_dir /
            "home"
        )

        # Basic XDG directories
        for name in [
            ".cache",
            ".config",
            ".local",
        ]:

            (
                home_dir /
                name
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        return True

    except OSError as e:

        print(
            "[ERROR] Could not create runner tree:"
        )

        print(
            f"        {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True,
        )

        return False

    except Exception as e:

        print(
            f"[ERROR] create_runner_tree(): {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True,
        )

        return False


# ============================================================
# RUNNER ENVIRONMENT
# ============================================================

def runner_environment(
    runner_dir: Path,
):
    """
    Make common runtime locations writable.
    """

    env = os.environ.copy()

    home_dir = (
        runner_dir /
        "home"
    )

    temp_dir = (
        runner_dir /
        "_temp"
    )

    tool_cache = (
        runner_dir /
        "_tool_cache"
    )

    env["HOME"] = str(
        home_dir
    )

    env["TMPDIR"] = str(
        temp_dir
    )

    env["TMP"] = str(
        temp_dir
    )

    env["TEMP"] = str(
        temp_dir
    )

    env["RUNNER_TEMP"] = str(
        temp_dir
    )

    env["RUNNER_TOOL_CACHE"] = str(
        tool_cache
    )

    env["AGENT_TOOLSDIRECTORY"] = str(
        tool_cache
    )

    env["XDG_CACHE_HOME"] = str(
        home_dir /
        ".cache"
    )

    env["XDG_CONFIG_HOME"] = str(
        home_dir /
        ".config"
    )

    env["XDG_DATA_HOME"] = str(
        home_dir /
        ".local" /
        "share"
    )

    return env


# ============================================================
# RUNNER CLEANUP
# ============================================================

def cleanup_runner(
    runner_name: str,
    runner_dir: Path,
):

    with state_lock:

        running_runners.pop(
            runner_name,
            None,
        )

    try:

        shutil.rmtree(
            runner_dir,
            ignore_errors=True,
        )

    except Exception:
        pass

    print(
        f"[RUNNER] {runner_name} cleaned."
    )


# ============================================================
# CREATE + START RUNNER
# ============================================================

def create_runner():

    global creating_runners
    global last_failure_time

    runner_id = uuid.uuid4().hex[:12]

    runner_name = (
        f"deplexo-{runner_id}"
    )

    runner_dir = (
        RUNNERS_DIR /
        runner_name
    )

    print()
    print(
        "=========================================="
    )

    print(
        "[RUNNER] Creating new runner"
    )

    print(
        f"[RUNNER] Name: {runner_name}"
    )

    print(
        f"[RUNNER] Repo: {GITHUB_SCOPE}"
    )

    print(
        f"[RUNNER] Dir:  {runner_dir}"
    )

    print(
        "=========================================="
    )

    try:

        # ----------------------------------------------------
        # Registration token
        # ----------------------------------------------------

        token = get_registration_token()

        if not token:

            print(
                "[RUNNER] Could not get registration token."
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # Create lightweight writable runner
        # ----------------------------------------------------

        print(
            f"[RUNNER] Preparing filesystem..."
        )

        if not create_runner_tree(
            runner_dir
        ):

            last_failure_time = time.time()

            return False

        env = runner_environment(
            runner_dir
        )

        # ----------------------------------------------------
        # Configure
        # ----------------------------------------------------

        config_command = [
            "./config.sh",

            "--url",
            f"https://github.com/{GITHUB_SCOPE}",

            "--token",
            token,

            "--name",
            runner_name,

            "--labels",
            RUNNER_LABELS,

            "--work",
            "_work",

            "--ephemeral",

            "--disableupdate",

            "--unattended",
        ]

        print(
            f"[RUNNER] Registering {runner_name}..."
        )

        result = subprocess.run(
            config_command,
            cwd=runner_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )

        output = result.stdout or ""

        print(output)

        if result.returncode != 0:

            print(
                "[ERROR] Runner registration failed."
            )

            print(
                f"[ERROR] Runner: {runner_name}"
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # Ensure registration actually created .runner
        # ----------------------------------------------------

        if not (
            runner_dir /
            ".runner"
        ).exists():

            print(
                "[ERROR] config.sh succeeded but .runner "
                "was not created."
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        print(
            f"[RUNNER] {runner_name} registered successfully."
        )

        # ----------------------------------------------------
        # Start runner
        # ----------------------------------------------------

        process = subprocess.Popen(
            [
                "./run.sh",
            ],
            cwd=runner_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with state_lock:

            running_runners[
                runner_name
            ] = {
                "process": process,
                "directory": runner_dir,
                "started": time.time(),
            }

        print(
            f"[RUNNER] {runner_name} started."
        )

        # ----------------------------------------------------
        # Monitor
        # ----------------------------------------------------

        def monitor():

            try:

                if process.stdout:

                    for line in process.stdout:

                        line = line.rstrip()

                        if line:

                            print(
                                f"[{runner_name}] "
                                f"{line}"
                            )

                process.wait()

            except Exception as e:

                print(
                    f"[ERROR] Monitor error "
                    f"{runner_name}: {e}"
                )

            finally:

                print()
                print(
                    f"[RUNNER] {runner_name} stopped."
                )

                cleanup_runner(
                    runner_name,
                    runner_dir,
                )

        thread = threading.Thread(
            target=monitor,
            daemon=True,
        )

        thread.start()

        return True

    except subprocess.TimeoutExpired:

        print(
            f"[ERROR] Configuration timeout: "
            f"{runner_name}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = time.time()

        return False

    except OSError as e:

        print(
            f"[ERROR] OS error for {runner_name}: {e}"
        )

        if getattr(
            e,
            "errno",
            None
        ) == 28:

            print(
                "[ERROR] No space left on device."
            )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = time.time()

        return False

    except Exception as e:

        print(
            f"[ERROR] Failed to start "
            f"{runner_name}: {e}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = time.time()

        return False

    finally:

        with state_lock:

            creating_runners -= 1


# ============================================================
# COUNTERS
# ============================================================

def local_runner_count():

    with state_lock:

        return len(
            running_runners
        )


def creating_runner_count():

    with state_lock:

        return creating_runners


# ============================================================
# START CREATION THREAD
# ============================================================

def start_runner_creation():

    global creating_runners

    with state_lock:

        if (
            creating_runners >=
            MAX_CREATING
        ):
            return False

        creating_runners += 1

    thread = threading.Thread(
        target=create_runner,
        daemon=True,
    )

    thread.start()

    return True


# ============================================================
# CLEAN OLD DIRECTORIES
# ============================================================

def clean_stale_directories():

    try:

        active = set()

        with state_lock:

            active = set(
                running_runners.keys()
            )

        for path in RUNNERS_DIR.iterdir():

            if not path.is_dir():
                continue

            if path.name in active:
                continue

            try:

                shutil.rmtree(
                    path,
                    ignore_errors=True,
                )

            except Exception:
                pass

    except Exception as e:

        print(
            f"[WARN] stale cleanup error: {e}"
        )


# ============================================================
# RECONCILIATION
# ============================================================

def ensure_target_count():

    global last_failure_time

    online = get_online_runners()

    if online is None:

        print(
            "[MANAGER] GitHub count unavailable."
        )

        return

    local = local_runner_count()

    creating = creating_runner_count()

    print()
    print(
        f"[MANAGER] "
        f"GitHub online={online} | "
        f"local={local} | "
        f"creating={creating} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # Target already reached
    # --------------------------------------------------------

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached."
        )

        print(
            "[MANAGER] No new runners needed."
        )

        return

    # --------------------------------------------------------
    # How many are missing?
    # --------------------------------------------------------

    missing = (
        TARGET_RUNNERS -
        online
    )

    # --------------------------------------------------------
    # Backoff after failed registration
    # --------------------------------------------------------

    if (
        last_failure_time > 0
        and
        (
            time.time() -
            last_failure_time
        ) < FAILURE_BACKOFF
    ):

        remaining = int(
            FAILURE_BACKOFF -
            (
                time.time() -
                last_failure_time
            )
        )

        if remaining < 0:
            remaining = 0

        print(
            f"[MANAGER] Failure backoff: "
            f"{remaining}s remaining."
        )

        return

    # --------------------------------------------------------
    # Already being created
    # --------------------------------------------------------

    effective_missing = (
        missing -
        creating
    )

    if effective_missing <= 0:

        print(
            "[MANAGER] Required runners are already "
            "being created."
        )

        return

    # --------------------------------------------------------
    # Limit simultaneous creations
    # --------------------------------------------------------

    available = (
        MAX_CREATING -
        creating
    )

    if available <= 0:

        print(
            "[MANAGER] Creation limit reached."
        )

        return

    create_count = min(
        effective_missing,
        available,
    )

    print()
    print(
        f"[MANAGER] Missing: {missing}"
    )

    print(
        f"[MANAGER] Starting: {create_count}"
    )

    for _ in range(
        create_count
    ):

        if stop_event.is_set():
            break

        if not start_runner_creation():
            break

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
    f"Labels       : {RUNNER_LABELS}"
)

print(
    f"Runtime dir  : {MANAGER_DIR}"
)

print(
    f"Runner source: {SOURCE_DIR}"
)

print(
    "=========================================="
)

print()


# ============================================================
# SOURCE CHECK
# ============================================================

if not validate_source():

    raise SystemExit(1)


# ============================================================
# CLEAN STARTUP
# ============================================================

clean_stale_directories()

show_storage()


# ============================================================
# HEALTH SERVER
# ============================================================

health_server = start_health_server()


# ============================================================
# INITIAL GITHUB CHECK
# ============================================================

print()
print(
    "[MANAGER] Initial GitHub runner check..."
)

initial_online = get_online_runners()

if initial_online is None:

    print(
        "[MANAGER] GitHub API unavailable."
    )

else:

    print(
        f"[MANAGER] Initial online runners: "
        f"{initial_online}"
    )

    if initial_online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target already reached."
        )

    else:

        missing = (
            TARGET_RUNNERS -
            initial_online
        )

        print(
            f"[MANAGER] Missing {missing} runner(s)."
        )


# ============================================================
# MAIN LOOP
# ============================================================

while not stop_event.is_set():

    try:

        print()
        print(
            "=========================================="
        )

        print(
            "[MANAGER] Checking runner count..."
        )

        print(
            "=========================================="
        )

        clean_stale_directories()

        ensure_target_count()

        show_storage()

    except Exception as e:

        print(
            f"[ERROR] Main loop error: {e}"
        )

    stop_event.wait(
        CHECK_INTERVAL
    )


# ============================================================
# SHUTDOWN
# ============================================================

print()
print(
    "[MANAGER] Stopping..."
)


with state_lock:

    processes = list(
        running_runners.values()
    )


for item in processes:

    process = item.get(
        "process"
    )

    if not process:
        continue

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
