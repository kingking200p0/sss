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

# Target repository.
# The manager project itself is NOT deployed here.
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

MAX_CREATING = int(
    os.environ.get("MAX_CREATING", "3")
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


# ============================================================
# PATHS
# ============================================================

# Deplexo runtime writable area.
MANAGER_DIR = Path(
    "/tmp/runner-manager"
)

RUNNERS_DIR = (
    MANAGER_DIR / "runners"
)

# Read-only official runner installation.
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
# SIGNALS
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
# TOKEN CHECK
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


# ============================================================
# WRITABLE RUNTIME DIRECTORY
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
        "[ERROR] Could not create runtime directory:"
    )

    print(
        f"        {e}"
    )

    raise SystemExit(1)


# ============================================================
# HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

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
            f"[HEALTH] Listening on port {PORT}"
        )

        return server

    except Exception as e:

        print(
            f"[WARN] Health server failed: {e}"
        )

        return None


# ============================================================
# GITHUB SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
)


# ============================================================
# GET ONLINE RUNNERS
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
                "[ERROR] GitHub runner list failed:"
            )

            print(
                f"        HTTP {response.status_code}"
            )

            print(
                response.text[:500]
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

        if response.status_code not in (200, 201):

            print(
                "[ERROR] Registration token failed:"
            )

            print(
                f"        HTTP {response.status_code}"
            )

            print(
                response.text[:500]
            )

            return None

        data = response.json()

        token = data.get(
            "token"
        )

        if not token:

            print(
                "[ERROR] No registration token returned."
            )

            return None

        return token

    except Exception as e:

        print(
            f"[ERROR] get_registration_token(): {e}"
        )

        return None


# ============================================================
# VALIDATE SOURCE
# ============================================================

def validate_source():

    required_files = [
        "config.sh",
        "run.sh",
        "env.sh",
        "run-helper.sh.template",
        "safe_sleep.sh",
    ]

    required_dirs = [
        "bin",
        "externals",
    ]

    print(
        "[MANAGER] Checking runner source..."
    )

    if not SOURCE_DIR.exists():

        print(
            f"[ERROR] Runner source missing: {SOURCE_DIR}"
        )

        return False

    for name in required_files:

        path = SOURCE_DIR / name

        if not path.is_file():

            print(
                f"[ERROR] Missing runner file: {path}"
            )

            return False

    for name in required_dirs:

        path = SOURCE_DIR / name

        if not path.is_dir():

            print(
                f"[ERROR] Missing runner directory: {path}"
            )

            return False

    print(
        "[MANAGER] Runner source OK."
    )

    return True


# ============================================================
# MAKE EXECUTABLE
# ============================================================

def make_executable(path: Path):

    try:

        mode = path.stat().st_mode

        path.chmod(
            mode | 0o111
        )

    except Exception:
        pass


# ============================================================
# CREATE RUNNER DIRECTORY
# ============================================================

def create_runner_tree(
    runner_dir: Path,
):

    """
    IMPORTANT:

    We DO NOT symlink shell scripts.

    config.sh/run.sh/env.sh resolve their own location.
    Therefore they must physically exist inside runner_dir.

    Large immutable directories:
        bin
        externals

    are symlinked to /opt/runner-source.

    Small script files are copied locally.
    """

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        # ----------------------------------------------------
        # COPY SMALL RUNNER FILES LOCALLY
        # ----------------------------------------------------

        local_files = [
            "config.sh",
            "run.sh",
            "env.sh",
            "run-helper.sh.template",
            "safe_sleep.sh",
        ]

        for name in local_files:

            source = (
                SOURCE_DIR / name
            )

            destination = (
                runner_dir / name
            )

            shutil.copy2(
                source,
                destination,
            )

            make_executable(
                destination
            )

        # ----------------------------------------------------
        # OPTIONAL SCRIPTS
        # ----------------------------------------------------

        optional_files = [
            "runsvc.sh",
            "svc.sh",
            "run-helper.cmd.template",
        ]

        for name in optional_files:

            source = (
                SOURCE_DIR / name
            )

            if source.is_file():

                destination = (
                    runner_dir / name
                )

                shutil.copy2(
                    source,
                    destination,
                )

                make_executable(
                    destination
                )

        # ----------------------------------------------------
        # LARGE STATIC DIRECTORIES
        # ----------------------------------------------------

        for name in [
            "bin",
            "externals",
        ]:

            source = (
                SOURCE_DIR / name
            )

            destination = (
                runner_dir / name
            )

            os.symlink(
                source,
                destination,
                target_is_directory=True,
            )

        # ----------------------------------------------------
        # LOCAL WRITABLE DIRECTORIES
        # ----------------------------------------------------

        for name in [
            "_work",
            "_diag",
            "_temp",
            "_tool_cache",
            "home",
        ]:

            (
                runner_dir / name
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        # ----------------------------------------------------
        # HOME
        # ----------------------------------------------------

        home = (
            runner_dir / "home"
        )

        for name in [
            ".cache",
            ".config",
            ".local",
            ".local/share",
        ]:

            (
                home / name
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        return True

    except Exception as e:

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


# ============================================================
# RUNNER ENVIRONMENT
# ============================================================

def runner_environment(
    runner_dir: Path,
):

    home = (
        runner_dir / "home"
    )

    temp = (
        runner_dir / "_temp"
    )

    tool_cache = (
        runner_dir / "_tool_cache"
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

    env["XDG_CACHE_HOME"] = str(
        home / ".cache"
    )

    env["XDG_CONFIG_HOME"] = str(
        home / ".config"
    )

    env["XDG_DATA_HOME"] = str(
        home / ".local" / "share"
    )

    return env


# ============================================================
# CLEAN RUNNER
# ============================================================

def cleanup_runner(
    runner_name,
    runner_dir,
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
        f"[RUNNER] Dir: {runner_dir}"
    )

    print(
        "=========================================="
    )

    try:

        # ----------------------------------------------------
        # TOKEN
        # ----------------------------------------------------

        token = get_registration_token()

        if not token:

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # FILESYSTEM
        # ----------------------------------------------------

        print(
            "[RUNNER] Creating runner filesystem..."
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
        # TEST THAT CONFIG.SH IS PHYSICALLY LOCAL
        # ----------------------------------------------------

        config_sh = (
            runner_dir / "config.sh"
        )

        if config_sh.is_symlink():

            print(
                "[ERROR] config.sh must NOT be a symlink."
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # REGISTER
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

        output = (
            result.stdout or ""
        )

        print(output)

        if result.returncode != 0:

            print(
                "[ERROR] Runner registration failed."
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # VERIFY
        # ----------------------------------------------------

        runner_config = (
            runner_dir / ".runner"
        )

        if not runner_config.is_file():

            print(
                "[ERROR] config.sh finished but "
                ".runner was not created."
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
        # START
        # ----------------------------------------------------

        run_sh = (
            runner_dir / "run.sh"
        )

        process = subprocess.Popen(
            [
                "./run.sh"
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
        # MONITOR
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
                    f"[ERROR] Monitor "
                    f"{runner_name}: {e}"
                )

            finally:

                print(
                    f"[RUNNER] "
                    f"{runner_name} stopped."
                )

                cleanup_runner(
                    runner_name,
                    runner_dir,
                )

        threading.Thread(
            target=monitor,
            daemon=True,
        ).start()

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
            f"[ERROR] OS error: {e}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = time.time()

        return False

    except Exception as e:

        print(
            f"[ERROR] Runner creation error: {e}"
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
# START CREATION
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

    threading.Thread(
        target=create_runner,
        daemon=True,
    ).start()

    return True


# ============================================================
# REMOVE STALE DIRS
# ============================================================

def clean_stale_directories():

    try:

        with state_lock:

            active = set(
                running_runners.keys()
            )

        for path in RUNNERS_DIR.iterdir():

            if not path.is_dir():
                continue

            if path.name in active:
                continue

            shutil.rmtree(
                path,
                ignore_errors=True,
            )

    except Exception as e:

        print(
            f"[WARN] Cleanup error: {e}"
        )


# ============================================================
# STORAGE
# ============================================================

def show_storage():

    try:

        usage = shutil.disk_usage(
            MANAGER_DIR
        )

        used = (
            usage.used /
            1024 /
            1024 /
            1024
        )

        free = (
            usage.free /
            1024 /
            1024 /
            1024
        )

        total = (
            usage.total /
            1024 /
            1024 /
            1024
        )

        print(
            f"[STORAGE] "
            f"Used={used:.2f}GB | "
            f"Free={free:.2f}GB | "
            f"Total={total:.2f}GB"
        )

    except Exception as e:

        print(
            f"[STORAGE] {e}"
        )


# ============================================================
# RECONCILIATION
# ============================================================

def ensure_target_count():

    global last_failure_time

    online = get_online_runners()

    if online is None:

        return

    local = local_runner_count()

    creating = creating_runner_count()

    print(
        f"[MANAGER] "
        f"GitHub online={online} | "
        f"local={local} | "
        f"creating={creating} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # TARGET REACHED
    # --------------------------------------------------------

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached."
        )

        return

    # --------------------------------------------------------
    # MISSING
    # --------------------------------------------------------

    missing = (
        TARGET_RUNNERS -
        online
    )

    # --------------------------------------------------------
    # BACKOFF
    # --------------------------------------------------------

    if last_failure_time:

        elapsed = (
            time.time() -
            last_failure_time
        )

        if elapsed < FAILURE_BACKOFF:

            remaining = int(
                FAILURE_BACKOFF -
                elapsed
            )

            print(
                f"[MANAGER] "
                f"Failure backoff: "
                f"{remaining}s"
            )

            return

    # --------------------------------------------------------
    # ACCOUNT FOR IN-FLIGHT CREATION
    # --------------------------------------------------------

    effective_missing = (
        missing -
        creating
    )

    if effective_missing <= 0:

        print(
            "[MANAGER] Required runners "
            "are already being created."
        )

        return

    available = (
        MAX_CREATING -
        creating
    )

    create_count = min(
        effective_missing,
        available,
    )

    print(
        f"[MANAGER] Missing "
        f"{missing} runner(s)."
    )

    print(
        f"[MANAGER] Starting "
        f"{create_count} creation(s)."
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
    f"Runtime      : {MANAGER_DIR}"
)

print(
    f"Source       : {SOURCE_DIR}"
)

print(
    "=========================================="
)

print()


# ============================================================
# SOURCE
# ============================================================

if not validate_source():

    raise SystemExit(1)


# ============================================================
# CLEAN
# ============================================================

clean_stale_directories()

show_storage()


# ============================================================
# HEALTH
# ============================================================

health_server = (
    start_health_server()
)


# ============================================================
# INITIAL CHECK
# ============================================================

print(
    "[MANAGER] Initial GitHub check..."
)

initial_online = (
    get_online_runners()
)

if initial_online is not None:

    print(
        f"[MANAGER] Initial online: "
        f"{initial_online}"
    )

    if initial_online < TARGET_RUNNERS:

        print(
            f"[MANAGER] Need "
            f"{TARGET_RUNNERS - initial_online} "
            f"runner(s)."
        )

    else:

        print(
            "[MANAGER] Target already reached."
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

with state_lock:

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
