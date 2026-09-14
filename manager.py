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

GITHUB_SCOPE = os.environ.get(
    "GITHUB_SCOPE",
    "kingking0020/mmm"
)

TARGET_RUNNERS = int(
    os.environ.get("TARGET_RUNNERS", "15")
)

CHECK_INTERVAL = int(
    os.environ.get("CHECK_INTERVAL", "15")
)

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo"
)

# Everything must be writable on Deplexo.
BASE_DIR = Path(
    os.environ.get(
        "RUNNER_BASE_DIR",
        "/tmp/runner-manager/runners"
    )
)

RUNNER_SOURCE = Path(
    "/home/runner"
)

API_BASE = "https://api.github.com"

REPO_API = (
    f"{API_BASE}/repos/{GITHUB_SCOPE}"
)

REGISTRATION_TOKEN_URL = (
    f"{REPO_API}/actions/runners/registration-token"
)

RUNNERS_URL = (
    f"{REPO_API}/actions/runners"
)


# ============================================================
# GLOBAL STATE
# ============================================================

running_runners = {}

lock = threading.Lock()

stop_event = threading.Event()


# ============================================================
# SIGNALS
# ============================================================

def shutdown_handler(signum, frame):

    print(
        "\n[MANAGER] Shutdown requested..."
    )

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

    print(
        "[ERROR] GG_TOKEN is not set."
    )

    raise SystemExit(1)


try:

    BASE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

except Exception as e:

    print(
        f"[ERROR] Cannot create BASE_DIR: {e}"
    )

    raise SystemExit(1)


# ============================================================
# GITHUB API
# ============================================================

def github_headers():

    return {
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


def get_online_runners():

    try:

        response = requests.get(
            RUNNERS_URL,
            headers=github_headers(),
            params={
                "per_page": 100
            },
            timeout=20,
        )

        if response.status_code != 200:

            print(
                "[ERROR] Failed to list runners: "
                f"{response.status_code} "
                f"{response.text}"
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

        print(
            f"[GITHUB] Registered: "
            f"{len(runners)} | "
            f"Online: {len(online)}"
        )

        return len(online)

    except Exception as e:

        print(
            f"[ERROR] get_online_runners(): {e}"
        )

        return None


def get_registration_token():

    try:

        response = requests.post(
            REGISTRATION_TOKEN_URL,
            headers=github_headers(),
            timeout=20,
        )

        if response.status_code not in (
            200,
            201
        ):

            print(
                "[ERROR] Registration token failed: "
                f"{response.status_code} "
                f"{response.text}"
            )

            return None

        data = response.json()

        token = data.get("token")

        if not token:

            print(
                "[ERROR] GitHub returned no "
                "registration token."
            )

            return None

        return token

    except Exception as e:

        print(
            f"[ERROR] get_registration_token(): {e}"
        )

        return None


# ============================================================
# PREPARE RUNNER
# ============================================================

def prepare_runner_directory(
    runner_dir
):

    """
    Create an isolated writable copy of the
    GitHub Actions runner.
    """

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False
        )

        print(
            f"[RUNNER] Copying runner files "
            f"to {runner_dir}"
        )

        shutil.copytree(
            RUNNER_SOURCE,
            runner_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "_diag",
                "_work",
                ".cache",
                ".local"
            )
        )

        # Writable runtime directories

        (runner_dir / "_diag").mkdir(
            parents=True,
            exist_ok=True
        )

        (runner_dir / "_work").mkdir(
            parents=True,
            exist_ok=True
        )

        return True

    except Exception as e:

        print(
            "[ERROR] Failed to prepare runner "
            f"directory: {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False


# ============================================================
# CREATE RUNNER
# ============================================================

def create_runner():

    runner_id = uuid.uuid4().hex[:12]

    runner_name = (
        f"deplexo-{runner_id}"
    )

    runner_dir = (
        BASE_DIR / runner_name
    )

    print()
    print("=" * 55)

    print(
        "[RUNNER] Creating new runner"
    )

    print(
        f"[RUNNER] Name: {runner_name}"
    )

    print(
        f"[RUNNER] Repo: {GITHUB_SCOPE}"
    )

    print("=" * 55)

    # --------------------------------------------------------
    # Prepare isolated writable directory
    # --------------------------------------------------------

    if not prepare_runner_directory(
        runner_dir
    ):

        return False

    # --------------------------------------------------------
    # Registration token
    # --------------------------------------------------------

    token = get_registration_token()

    if not token:

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    # --------------------------------------------------------
    # Environment
    # --------------------------------------------------------

    runner_env = os.environ.copy()

    # Critical:
    # HOME must be writable.

    runner_env["HOME"] = str(
        runner_dir
    )

    runner_env["RUNNER_HOME"] = str(
        runner_dir
    )

    runner_env["DOTNET_CLI_HOME"] = str(
        runner_dir
    )

    runner_env["TMPDIR"] = str(
        runner_dir / "_tmp"
    )

    runner_env["TEMP"] = str(
        runner_dir / "_tmp"
    )

    runner_env["TMP"] = str(
        runner_dir / "_tmp"
    )

    (runner_dir / "_tmp").mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Configure
    # --------------------------------------------------------

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

    try:

        print(
            f"[RUNNER] Registering "
            f"{runner_name}..."
        )

        result = subprocess.run(

            config_command,

            cwd=runner_dir,

            env=runner_env,

            stdout=subprocess.PIPE,

            stderr=subprocess.STDOUT,

            text=True,

            timeout=120,
        )

        print(
            result.stdout
        )

        if result.returncode != 0:

            print(
                "[ERROR] Runner registration failed: "
                f"{runner_name}"
            )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            return False

    except subprocess.TimeoutExpired:

        print(
            "[ERROR] Runner configuration timeout."
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    except Exception as e:

        print(
            f"[ERROR] Runner configuration error: {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    print(
        f"[RUNNER] {runner_name} "
        "registered successfully."
    )

    # --------------------------------------------------------
    # Start runner
    # --------------------------------------------------------

    try:

        process = subprocess.Popen(

            [
                "./run.sh"
            ],

            cwd=runner_dir,

            env=runner_env,

            stdout=subprocess.PIPE,

            stderr=subprocess.STDOUT,

            text=True,

            bufsize=1,
        )

    except Exception as e:

        print(
            f"[ERROR] Failed to start "
            f"{runner_name}: {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    # --------------------------------------------------------
    # Save process
    # --------------------------------------------------------

    with lock:

        running_runners[
            runner_name
        ] = {

            "process": process,

            "directory": runner_dir,
        }

    print(
        f"[RUNNER] {runner_name} started."
    )

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

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
                f"[ERROR] Monitoring "
                f"{runner_name}: {e}"
            )

        finally:

            print(
                f"[RUNNER] {runner_name} stopped."
            )

            with lock:

                running_runners.pop(
                    runner_name,
                    None
                )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            print(
                f"[RUNNER] {runner_name} cleaned."
            )

    thread = threading.Thread(
        target=monitor,
        daemon=True
    )

    thread.start()

    return True


# ============================================================
# LOCAL COUNT
# ============================================================

def local_runner_count():

    with lock:

        return len(
            running_runners
        )


# ============================================================
# RECONCILE
# ============================================================

def ensure_target_count():

    online = get_online_runners()

    if online is None:

        print(
            "[MANAGER] Could not determine "
            "GitHub runner count."
        )

        return

    local = local_runner_count()

    print(
        f"[MANAGER] GitHub online={online} | "
        f"local={local} | "
        f"target={TARGET_RUNNERS}"
    )

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached."
        )

        return

    missing = (
        TARGET_RUNNERS - online
    )

    print(
        f"[MANAGER] Missing {missing} runner(s)."
    )

    for i in range(missing):

        if stop_event.is_set():

            break

        print(
            f"[MANAGER] Creating "
            f"{i + 1}/{missing}"
        )

        create_runner()

        time.sleep(1)


# ============================================================
# START
# ============================================================

print()

print("=" * 60)

print(
    "       DEPLEXO GITHUB RUNNER MANAGER"
)

print("=" * 60)

print(
    f"Target repo : {GITHUB_SCOPE}"
)

print(
    f"Target      : {TARGET_RUNNERS}"
)

print(
    f"Interval    : {CHECK_INTERVAL}s"
)

print(
    f"Labels      : {RUNNER_LABELS}"
)

print(
    f"Base dir    : {BASE_DIR}"
)

print("=" * 60)

print()


# ============================================================
# INITIAL CHECK
# ============================================================

print(
    "[MANAGER] Initial runner check..."
)

ensure_target_count()


# ============================================================
# MAIN LOOP
# ============================================================

while not stop_event.is_set():

    try:

        print()

        print(
            "[MANAGER] Checking runner count..."
        )

        ensure_target_count()

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

print(
    "[MANAGER] Stopping manager..."
)

with lock:

    processes = list(
        running_runners.values()
    )


for runner in processes:

    try:

        runner["process"].terminate()

    except Exception:

        pass


print(
    "[MANAGER] Shutdown complete."
)
