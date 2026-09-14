import os
import time
import uuid
import shutil
import signal
import subprocess
import threading
from pathlib import Path

import requests


# ============================================================
# CONFIG
# ============================================================

GITHUB_TOKEN = os.environ.get("GG_TOKEN")

# IMPORTANT:
# This is the TARGET repository.
# The manager itself is NOT deployed here.
GITHUB_SCOPE = os.environ.get(
    "GITHUB_SCOPE",
    "kingking0020/mmm"
)

TARGET_RUNNERS = int(
    os.environ.get("TARGET_RUNNERS", "15")
)

CHECK_INTERVAL = int(
    os.environ.get("CHECK_INTERVAL", "20")
)

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo"
)

# Maximum number of runner creation operations that can
# happen simultaneously.
MAX_CREATING = int(
    os.environ.get("MAX_CREATING", "3")
)

# Delay after a failed creation.
FAILURE_BACKOFF = int(
    os.environ.get("FAILURE_BACKOFF", "30")
)


# ============================================================
# DIRECTORIES
# ============================================================

MANAGER_DIR = Path(
    os.environ.get(
        "RUNNER_MANAGER_DIR",
        "/runner-manager"
    )
)

SOURCE_DIR = Path("/opt/runner-source")

RUNNERS_DIR = MANAGER_DIR / "runners"

BASE_COPY_DIR = MANAGER_DIR / "base"

RUNNERS_DIR.mkdir(
    parents=True,
    exist_ok=True
)

BASE_COPY_DIR.mkdir(
    parents=True,
    exist_ok=True
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

running_runners = {}

creating_runners = 0

state_lock = threading.Lock()

stop_event = threading.Event()

last_failure_time = 0


# ============================================================
# SIGNAL HANDLERS
# ============================================================

def shutdown_handler(signum, frame):
    print()
    print("[MANAGER] Shutdown requested...")
    stop_event.set()


signal.signal(
    signal.SIGTERM,
    shutdown_handler
)

signal.signal(
    signal.SIGINT,
    shutdown_handler
)


# ============================================================
# VALIDATION
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


# ============================================================
# GITHUB HEADERS
# ============================================================

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    github_headers()
)


# ============================================================
# GITHUB RUNNER COUNT
# ============================================================

def get_online_runners():
    """
    Return the number of online runners in the target repo.

    None means the GitHub API could not be queried.
    """

    try:
        response = session.get(
            RUNNERS_URL,
            params={
                "per_page": 100
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
            []
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
# REGISTRATION TOKEN
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
# CLEAN OLD MANAGER DATA
# ============================================================

def clean_old_data():

    """
    Remove incomplete runner directories.

    IMPORTANT:
    Never remove currently running runners.
    """

    with state_lock:

        active_names = set(
            running_runners.keys()
        )

    for path in RUNNERS_DIR.iterdir():

        if not path.is_dir():
            continue

        if path.name in active_names:
            continue

        try:

            shutil.rmtree(
                path,
                ignore_errors=True
            )

        except Exception:
            pass


# ============================================================
# CREATE BASE RUNNER TREE
# ============================================================

def ensure_base_copy():

    """
    Make ONE full writable copy of the runner installation.

    All additional runners use hard links from this copy.

    This is much cheaper than making 15 complete copies.
    """

    marker = (
        BASE_COPY_DIR / ".base-ready"
    )

    if marker.exists():

        print(
            "[MANAGER] Base runner already prepared."
        )

        return True

    print()
    print(
        "[MANAGER] Preparing base runner installation..."
    )

    print(
        f"[MANAGER] Source: {SOURCE_DIR}"
    )

    print(
        f"[MANAGER] Base:   {BASE_COPY_DIR}"
    )

    try:

        if not SOURCE_DIR.exists():

            print(
                "[ERROR] Runner source directory does not exist:"
            )

            print(
                f"        {SOURCE_DIR}"
            )

            return False

        # Clean incomplete base.
        if BASE_COPY_DIR.exists():

            for item in BASE_COPY_DIR.iterdir():

                if item.name == ".base-ready":
                    continue

                if item.is_dir():
                    shutil.rmtree(
                        item,
                        ignore_errors=True
                    )
                else:
                    try:
                        item.unlink()
                    except Exception:
                        pass

        # Full copy ONLY ONCE.
        shutil.copytree(
            SOURCE_DIR,
            BASE_COPY_DIR,
            dirs_exist_ok=True
        )

        # Make sure the runner files can be executed.
        for script_name in (
            "config.sh",
            "run.sh",
            "env.sh",
        ):

            script = (
                BASE_COPY_DIR /
                script_name
            )

            if script.exists():

                try:
                    mode = script.stat().st_mode

                    script.chmod(
                        mode | 0o111
                    )

                except Exception:
                    pass

        marker.touch()

        print(
            "[MANAGER] Base runner prepared successfully."
        )

        return True

    except OSError as e:

        print(
            "[ERROR] Could not prepare base runner:"
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

        return False

    except Exception as e:

        print(
            f"[ERROR] Base runner preparation failed: {e}"
        )

        return False


# ============================================================
# HARD-LINK RUNNER TREE
# ============================================================

def create_linked_runner_tree(
    runner_dir: Path
):
    """
    Create a runner directory using hard links.

    This avoids making 15 copies of the large GitHub Runner
    installation.

    The runner-specific files such as:
        .runner
        .credentials
        .env
        .path
        _diag
        _work
    remain unique to each runner.
    """

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False
        )

        # cp -al creates hardlinks for files and copies
        # directory entries.
        #
        # It is extremely space efficient when source and
        # destination are on the same filesystem.
        result = subprocess.run(
            [
                "cp",
                "-al",
                f"{BASE_COPY_DIR}/.",
                str(runner_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:

            print(
                "[ERROR] cp -al failed:"
            )

            print(
                result.stdout
            )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            return False

        return True

    except Exception as e:

        print(
            f"[ERROR] create_linked_runner_tree(): {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False


# ============================================================
# RUNNER
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

        token = get_registration_token()

        if not token:

            print(
                "[RUNNER] No registration token."
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # Create hard-linked installation
        # ----------------------------------------------------

        print(
            f"[RUNNER] Preparing files for {runner_name}..."
        )

        if not create_linked_runner_tree(
            runner_dir
        ):

            print(
                "[ERROR] Could not create runner filesystem."
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # config.sh must run from the runner's own directory.
        # This directory is writable.
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )

        output = result.stdout or ""

        print(output)

        if result.returncode != 0:

            print(
                "[ERROR] Runner registration failed:"
            )

            print(
                f"        {runner_name}"
            )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            last_failure_time = time.time()

            return False

        # ----------------------------------------------------
        # Verify registration file
        # ----------------------------------------------------

        runner_config = (
            runner_dir /
            ".runner"
        )

        if not runner_config.exists():

            print(
                "[ERROR] config.sh returned success "
                "but .runner does not exist."
            )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            last_failure_time = time.time()

            return False

        print()
        print(
            f"[RUNNER] {runner_name} registered successfully."
        )

        # ----------------------------------------------------
        # Start runner
        # ----------------------------------------------------

        process = subprocess.Popen(
            [
                "./run.sh"
            ],

            cwd=runner_dir,

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
            f"[RUNNER] {runner_name} process started."
        )

        # ----------------------------------------------------
        # Monitor
        # ----------------------------------------------------

        def monitor_runner():

            try:

                if process.stdout:

                    for line in process.stdout:

                        line = line.rstrip()

                        if line:

                            print(
                                f"[{runner_name}] {line}"
                            )

                process.wait()

            except Exception as e:

                print(
                    f"[ERROR] Monitoring {runner_name}: {e}"
                )

            finally:

                print()
                print(
                    f"[RUNNER] {runner_name} stopped."
                )

                with state_lock:

                    running_runners.pop(
                        runner_name,
                        None
                    )

                # The runner is ephemeral.
                #
                # GitHub automatically deregisters an
                # ephemeral runner after its one job.
                #
                # Remove local files too.
                shutil.rmtree(
                    runner_dir,
                    ignore_errors=True
                )

                print(
                    f"[RUNNER] {runner_name} cleaned."
                )

        thread = threading.Thread(
            target=monitor_runner,
            daemon=True,
        )

        thread.start()

        return True

    except subprocess.TimeoutExpired:

        print(
            f"[ERROR] Runner configuration timeout: "
            f"{runner_name}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        last_failure_time = time.time()

        return False

    except OSError as e:

        print(
            f"[ERROR] OS error creating {runner_name}: {e}"
        )

        if getattr(
            e,
            "errno",
            None
        ) == 28:

            print(
                "[ERROR] No space left on device."
            )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        last_failure_time = time.time()

        return False

    except Exception as e:

        print(
            f"[ERROR] Failed to start runner "
            f"{runner_name}: {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        last_failure_time = time.time()

        return False

    finally:

        with state_lock:

            creating_runners -= 1


# ============================================================
# LOCAL RUNNER COUNT
# ============================================================

def local_runner_count():

    with state_lock:

        return len(
            running_runners
        )


# ============================================================
# CURRENT CREATION COUNT
# ============================================================

def current_creating_count():

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

    creating = current_creating_count()

    print()
    print(
        f"[MANAGER] GitHub online={online} | "
        f"local={local} | "
        f"creating={creating} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # Already enough runners
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
    # Missing count
    # --------------------------------------------------------

    missing = (
        TARGET_RUNNERS -
        online
    )

    # --------------------------------------------------------
    # Do not hammer GitHub if previous attempts failed.
    # --------------------------------------------------------

    if (
        last_failure_time > 0
        and
        time.time() - last_failure_time
        < FAILURE_BACKOFF
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
            f"[MANAGER] Creation backoff active. "
            f"Waiting {remaining}s."
        )

        return

    # --------------------------------------------------------
    # Account for runners currently being created.
    # --------------------------------------------------------

    effective_missing = (
        missing -
        creating
    )

    if effective_missing <= 0:

        print(
            "[MANAGER] Missing runners are already "
            "being created."
        )

        return

    # --------------------------------------------------------
    # Limit simultaneous creations.
    # --------------------------------------------------------

    available_slots = (
        MAX_CREATING -
        creating
    )

    create_count = min(
        effective_missing,
        available_slots
    )

    print()
    print(
        f"[MANAGER] Missing {missing} runner(s)."
    )

    print(
        f"[MANAGER] Starting {create_count} creation(s)."
    )

    for _ in range(create_count):

        if stop_event.is_set():
            break

        started = start_runner_creation()

        if not started:
            break

        time.sleep(2)


# ============================================================
# STORAGE INFORMATION
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
            f"[STORAGE] Used: {used_gb:.2f} GB | "
            f"Free: {free_gb:.2f} GB | "
            f"Total: {total_gb:.2f} GB"
        )

    except Exception as e:

        print(
            f"[STORAGE] Unable to read disk usage: {e}"
        )


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
    f"Manager repo : kingking200p0/sss"
)

print(
    f"Target repo  : {GITHUB_SCOPE}"
)

print(
    f"Target       : {TARGET_RUNNERS} runners"
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
    f"Manager dir  : {MANAGER_DIR}"
)

print(
    "=========================================="
)

print()


# ============================================================
# CLEAN OLD DATA
# ============================================================

print(
    "[MANAGER] Cleaning incomplete old runner directories..."
)

clean_old_data()


# ============================================================
# PREPARE BASE
# ============================================================

if not ensure_base_copy():

    print(
        "[MANAGER] Cannot prepare runner base."
    )

    print(
        "[MANAGER] Manager will stop instead of "
        "entering an infinite disk-filling loop."
    )

    raise SystemExit(1)


show_storage()


# ============================================================
# INITIAL RECONCILIATION
# ============================================================

print()
print(
    "[MANAGER] Performing initial GitHub runner check..."
)

initial_online = get_online_runners()

if initial_online is None:

    print(
        "[MANAGER] GitHub API unavailable at startup."
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
            f"[MANAGER] Need {missing} runner(s)."
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
    "[MANAGER] Stopping manager..."
)


with state_lock:

    processes = list(
        running_runners.values()
    )


for runner in processes:

    process = runner.get(
        "process"
    )

    if not process:
        continue

    try:

        process.terminate()

    except Exception:
        pass


print(
    "[MANAGER] Shutdown complete."
)
