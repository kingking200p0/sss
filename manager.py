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


# ============================================================
# DIRECTORIES
# ============================================================

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

# Read-only source from image
SOURCE_DIR = Path(
    "/opt/runner-source"
)

# One copy of bin on the SAME filesystem as /tmp runners.
SHARED_DIR = (
    MANAGER_DIR /
    "shared"
)

SHARED_BIN = (
    SHARED_DIR /
    "bin"
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
# TOKEN
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


# ============================================================
# CREATE WRITABLE DIRS
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

    SHARED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

except Exception as e:

    print(
        "[ERROR] Cannot initialize /tmp:"
    )

    print(
        f"        {e}"
    )

    raise SystemExit(1)


# ============================================================
# GITHUB SESSION
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
            "text/plain; charset=utf-8",
        )

        self.end_headers()

        self.wfile.write(
            b"OK\n"
        )

    def log_message(self, format, *args):
        return


def start_health():

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
# STORAGE
# ============================================================

def storage_info():

    try:

        u = shutil.disk_usage(
            MANAGER_DIR
        )

        print(
            "[STORAGE] "
            f"Used={u.used / 1024**3:.3f}GB | "
            f"Free={u.free / 1024**3:.3f}GB | "
            f"Total={u.total / 1024**3:.3f}GB"
        )

        return u.free

    except Exception as e:

        print(
            f"[STORAGE] Error: {e}"
        )

        return 0


# ============================================================
# SOURCE VALIDATION
# ============================================================

def validate_source():

    required_files = [
        "config.sh",
        "run.sh",
        "env.sh",
    ]

    required_dirs = [
        "bin",
        "externals",
    ]

    print(
        "[MANAGER] Checking runner source..."
    )

    if not SOURCE_DIR.is_dir():

        print(
            f"[ERROR] Missing source: "
            f"{SOURCE_DIR}"
        )

        return False

    for name in required_files:

        path = (
            SOURCE_DIR /
            name
        )

        if not path.is_file():

            print(
                f"[ERROR] Missing file: "
                f"{path}"
            )

            return False

    for name in required_dirs:

        path = (
            SOURCE_DIR /
            name
        )

        if not path.is_dir():

            print(
                f"[ERROR] Missing directory: "
                f"{path}"
            )

            return False

    listener = (
        SOURCE_DIR /
        "bin" /
        "Runner.Listener"
    )

    if not listener.is_file():

        print(
            f"[ERROR] Missing Runner.Listener: "
            f"{listener}"
        )

        return False

    print(
        "[MANAGER] Runner source OK."
    )

    return True


# ============================================================
# PREPARE SHARED BIN
# ============================================================

def prepare_shared_bin():

    """
    Copy bin ONCE from /opt to /tmp.

    /opt and /tmp are different filesystems, so we do one
    normal copy here.

    Every individual runner then uses hard-links to these files
    because both are inside /tmp and therefore same filesystem.
    """

    marker = (
        SHARED_BIN /
        ".ready"
    )

    if marker.exists():

        print(
            "[MANAGER] Shared bin already prepared."
        )

        return True

    print()
    print(
        "[MANAGER] Preparing shared Runner bin..."
    )

    source_bin = (
        SOURCE_DIR /
        "bin"
    )

    try:

        # ----------------------------------------------------
        # Remove any incomplete previous copy.
        # ----------------------------------------------------

        if SHARED_BIN.exists():

            shutil.rmtree(
                SHARED_BIN,
                ignore_errors=True,
            )

        # ----------------------------------------------------
        # Check free space first.
        # ----------------------------------------------------

        free = storage_info()

        # We leave a safety margin because Runner also needs
        # space for config/diagnostics/work files.
        if free < 30 * 1024 * 1024:

            print(
                "[ERROR] Less than 30MB writable space."
            )

            return False

        # ----------------------------------------------------
        # Copy bin once.
        # ----------------------------------------------------

        shutil.copytree(
            source_bin,
            SHARED_BIN,
        )

        # ----------------------------------------------------
        # Remove unnecessary source-control metadata if any.
        # ----------------------------------------------------

        for path in SHARED_BIN.rglob(
            ".DS_Store"
        ):

            try:
                path.unlink()
            except Exception:
                pass

        marker.touch()

        print(
            "[MANAGER] Shared bin prepared."
        )

        storage_info()

        return True

    except OSError as e:

        print(
            "[ERROR] Shared bin copy failed:"
        )

        print(
            f"        {e}"
        )

        if getattr(
            e,
            "errno",
            None
        ) == 28:

            print(
                "[ERROR] No space left on device."
            )

            print(
                "[ERROR] Deplexo writable space is "
                "too small even for one local Runner bin."
            )

        shutil.rmtree(
            SHARED_BIN,
            ignore_errors=True,
        )

        return False

    except Exception as e:

        print(
            f"[ERROR] Shared bin error: {e}"
        )

        shutil.rmtree(
            SHARED_BIN,
            ignore_errors=True,
        )

        return False


# ============================================================
# HARDLINK DIRECTORY
# ============================================================

def hardlink_tree(
    source: Path,
    destination: Path,
):

    """
    Create a directory tree whose regular files are hard-links.

    Both source and destination are under /tmp, so they are on
    the same filesystem.

    No extra file-data blocks are consumed for each runner.
    """

    destination.mkdir(
        parents=True,
        exist_ok=False,
    )

    for item in source.iterdir():

        target = (
            destination /
            item.name
        )

        if item.is_dir():

            hardlink_tree(
                item,
                target,
            )

        elif item.is_file():

            os.link(
                item,
                target,
            )

        elif item.is_symlink():

            os.symlink(
                os.readlink(item),
                target,
            )


# ============================================================
# CREATE RUNNER FILESYSTEM
# ============================================================

def create_runner_filesystem(
    runner_dir: Path,
):

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        # ----------------------------------------------------
        # Small root files.
        # ----------------------------------------------------

        for item in SOURCE_DIR.iterdir():

            if not item.is_file():
                continue

            destination = (
                runner_dir /
                item.name
            )

            shutil.copy2(
                item,
                destination,
            )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # bin is HARD-LINKED from /tmp/shared/bin.
        #
        # Runner.Listener therefore physically exists under:
        #
        # /tmp/runner-manager/runners/<name>/bin/...
        #
        # and GitHub Runner derives Root/Diag from there.
        # ----------------------------------------------------

        local_bin = (
            runner_dir /
            "bin"
        )

        hardlink_tree(
            SHARED_BIN,
            local_bin,
        )

        # ----------------------------------------------------
        # externals stays shared/read-only.
        # ----------------------------------------------------

        externals_source = (
            SOURCE_DIR /
            "externals"
        )

        externals_target = (
            runner_dir /
            "externals"
        )

        os.symlink(
            str(externals_source),
            str(externals_target),
            target_is_directory=True,
        )

        # ----------------------------------------------------
        # Writable directories.
        # ----------------------------------------------------

        for name in [
            "_diag",
            "_work",
            "_temp",
            "_tool_cache",
        ]:

            (
                runner_dir /
                name
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        # ----------------------------------------------------
        # Writable HOME.
        # ----------------------------------------------------

        home = (
            runner_dir /
            "_home"
        )

        for relative in [
            ".cache",
            ".config",
            ".local",
            ".local/share",
        ]:

            (
                home /
                relative
            ).mkdir(
                parents=True,
                exist_ok=True,
            )

        # ----------------------------------------------------
        # Verify paths.
        # ----------------------------------------------------

        listener = (
            runner_dir /
            "bin" /
            "Runner.Listener"
        )

        listener_dll = (
            runner_dir /
            "bin" /
            "Runner.Listener.dll"
        )

        config = (
            runner_dir /
            "config.sh"
        )

        run = (
            runner_dir /
            "run.sh"
        )

        if not listener.is_file():

            raise RuntimeError(
                "Local Runner.Listener was not created."
            )

        if not listener_dll.is_file():

            raise RuntimeError(
                "Local Runner.Listener.dll was not created."
            )

        if not config.is_file():

            raise RuntimeError(
                "Local config.sh was not created."
            )

        if not run.is_file():

            raise RuntimeError(
                "Local run.sh was not created."
            )

        print(
            "[RUNNER] Local Runner.Listener:"
        )

        print(
            f"        {listener}"
        )

        print(
            "[RUNNER] Local Runner.Listener.dll:"
        )

        print(
            f"        {listener_dll}"
        )

        return True

    except Exception as e:

        print(
            "[ERROR] Filesystem creation failed:"
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
# ENVIRONMENT
# ============================================================

def build_runner_env(
    runner_dir: Path,
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
        home /
        ".cache"
    )

    env["XDG_CONFIG_HOME"] = str(
        home /
        ".config"
    )

    env["XDG_DATA_HOME"] = str(
        home /
        ".local" /
        "share"
    )

    return env


# ============================================================
# CLEANUP
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

    storage_info()


# ============================================================
# CREATE RUNNER
# ============================================================

def create_runner():

    global creating_runners
    global last_failure_time

    runner_name = (
        "deplexo-" +
        uuid.uuid4().hex[:12]
    )

    runner_dir = (
        RUNNERS_DIR /
        runner_name
    )

    try:

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

        # ----------------------------------------------------
        # Registration token
        # ----------------------------------------------------

        token = (
            get_registration_token()
        )

        if not token:

            last_failure_time = (
                time.time()
            )

            return False

        # ----------------------------------------------------
        # Filesystem
        # ----------------------------------------------------

        print(
            "[RUNNER] Creating runner filesystem..."
        )

        if not create_runner_filesystem(
            runner_dir
        ):

            last_failure_time = (
                time.time()
            )

            return False

        env = build_runner_env(
            runner_dir
        )

        config = (
            runner_dir /
            "config.sh"
        )

        run = (
            runner_dir /
            "run.sh"
        )

        # ----------------------------------------------------
        # REGISTER
        # ----------------------------------------------------

        print(
            f"[RUNNER] Registering "
            f"{runner_name}..."
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
                runner_name,

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
                "[ERROR] Registration failed."
            )

            print(
                f"[ERROR] Exit code: "
                f"{result.returncode}"
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = (
                time.time()
            )

            return False

        # ----------------------------------------------------
        # CONFIG CREATED?
        # ----------------------------------------------------

        if not (
            runner_dir /
            ".runner"
        ).is_file():

            print(
                "[ERROR] .runner was not created."
            )

            cleanup_runner(
                runner_name,
                runner_dir,
            )

            last_failure_time = (
                time.time()
            )

            return False

        print(
            f"[RUNNER] "
            f"{runner_name} registered successfully."
        )

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        print(
            f"[RUNNER] Starting "
            f"{runner_name}..."
        )

        process = subprocess.Popen(
            [
                "bash",
                str(run),
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
            f"[RUNNER] "
            f"{runner_name} started."
        )

        # ----------------------------------------------------
        # MONITOR
        # ----------------------------------------------------

        def monitor():

            try:

                if process.stdout:

                    for line in process.stdout:

                        line = (
                            line.rstrip()
                        )

                        if line:

                            print(
                                f"[{runner_name}] "
                                f"{line}"
                            )

                process.wait()

            except Exception as e:

                print(
                    f"[ERROR] "
                    f"{runner_name} monitor: "
                    f"{e}"
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
            f"[ERROR] Timeout configuring "
            f"{runner_name}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = (
            time.time()
        )

        return False

    except OSError as e:

        print(
            f"[ERROR] OS error: {e}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = (
            time.time()
        )

        return False

    except Exception as e:

        print(
            f"[ERROR] Runner error: {e}"
        )

        cleanup_runner(
            runner_name,
            runner_dir,
        )

        last_failure_time = (
            time.time()
        )

        return False

    finally:

        with state_lock:

            creating_runners -= 1


# ============================================================
# START CREATION
# ============================================================

def start_creation():

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
# COUNTERS
# ============================================================

def local_count():

    with state_lock:

        return len(
            running_runners
        )


def creating_count():

    with state_lock:

        return creating_runners


# ============================================================
# RECONCILIATION
# ============================================================

def reconcile():

    global last_failure_time

    online = (
        get_online_runners()
    )

    if online is None:
        return

    local = local_count()

    creating = creating_count()

    print(
        f"[MANAGER] "
        f"GitHub online={online} | "
        f"local={local} | "
        f"creating={creating} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # Enough
    # --------------------------------------------------------

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached."
        )

        return

    # --------------------------------------------------------
    # Missing
    # --------------------------------------------------------

    missing = (
        TARGET_RUNNERS -
        online
    )

    # --------------------------------------------------------
    # Failure backoff
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # In-flight
    # --------------------------------------------------------

    effective_missing = (
        missing -
        creating
    )

    if effective_missing <= 0:

        return

    # --------------------------------------------------------
    # Limit
    # --------------------------------------------------------

    available = (
        MAX_CREATING -
        creating
    )

    count = min(
        effective_missing,
        available,
    )

    print(
        f"[MANAGER] Missing "
        f"{missing} runner(s)."
    )

    print(
        f"[MANAGER] Creating "
        f"{count} runner(s)."
    )

    for _ in range(count):

        if stop_event.is_set():
            return

        if not start_creation():
            return

        time.sleep(2)


# ============================================================
# GITHUB ONLINE
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
                "[ERROR] GitHub runner list:"
            )

            print(
                f"        HTTP "
                f"{response.status_code}"
            )

            print(
                response.text[:500]
            )

            return None

        data = (
            response.json()
        )

        runners = (
            data.get(
                "runners",
                []
            )
        )

        online = [
            r for r in runners
            if r.get("status") == "online"
        ]

        busy = [
            r for r in online
            if r.get("busy") is True
        ]

        print(
            f"[GITHUB] "
            f"Registered={len(runners)} | "
            f"Online={len(online)} | "
            f"Busy={len(busy)}"
        )

        return len(online)

    except Exception as e:

        print(
            f"[ERROR] GitHub list: {e}"
        )

        return None


# ============================================================
# TOKEN
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
                "[ERROR] Registration token:"
            )

            print(
                f"        HTTP "
                f"{response.status_code}"
            )

            print(
                response.text[:500]
            )

            return None

        data = (
            response.json()
        )

        return data.get(
            "token"
        )

    except Exception as e:

        print(
            f"[ERROR] Token request: {e}"
        )

        return None


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
    f"Shared bin   : {SHARED_BIN}"
)

print(
    "=========================================="
)

print()


# ============================================================
# VALIDATE
# ============================================================

if not validate_source():

    raise SystemExit(1)


# ============================================================
# HEALTH
# ============================================================

health_server = (
    start_health()
)


# ============================================================
# PREPARE SHARED BIN
# ============================================================

if not prepare_shared_bin():

    print(
        "[MANAGER] Shared bin could not be prepared."
    )

    print(
        "[MANAGER] Stopping safely."
    )

    raise SystemExit(1)


# ============================================================
# INITIAL CLEANUP
# ============================================================

try:

    for path in RUNNERS_DIR.iterdir():

        if path.is_dir():

            shutil.rmtree(
                path,
                ignore_errors=True,
            )

except Exception:
    pass


storage_info()


# ============================================================
# INITIAL GITHUB STATUS
# ============================================================

print(
    "[MANAGER] Initial GitHub check..."
)

initial = (
    get_online_runners()
)

if initial is not None:

    print(
        f"[MANAGER] "
        f"Initial online={initial}"
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

        reconcile()

        storage_info()

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

    process = (
        item.get("process")
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
