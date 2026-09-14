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

# Target repository where runners will be registered
GITHUB_SCOPE = os.environ.get(
    "GITHUB_SCOPE",
    "kingking0020/mmm"
)

# Desired number of ONLINE runners
TARGET_RUNNERS = int(
    os.environ.get("TARGET_RUNNERS", "15")
)

# Check interval in seconds
CHECK_INTERVAL = int(
    os.environ.get("CHECK_INTERVAL", "15")
)

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo"
)

# Deplexo filesystem is read-only outside writable locations.
# /tmp is writable.
BASE_DIR = Path(
    os.environ.get(
        "RUNNER_BASE_DIR",
        "/tmp/runner-manager/runners"
    )
)

API_BASE = "https://api.github.com"

REPO_API = f"{API_BASE}/repos/{GITHUB_SCOPE}"

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
    print("\n[MANAGER] Shutdown requested...")
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


try:

    BASE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

except Exception as e:

    print(
        f"[ERROR] Cannot create runner directory: {e}"
    )

    raise SystemExit(1)


# ============================================================
# GITHUB API
# ============================================================

def github_headers():

    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }


def get_online_runners():

    """
    Get the number of ONLINE runners
    registered in the target repository.
    """

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

        online_runners = [
            runner
            for runner in runners
            if runner.get("status") == "online"
        ]

        print(
            f"[GITHUB] Registered: {len(runners)} | "
            f"Online: {len(online_runners)}"
        )

        return len(online_runners)

    except Exception as e:

        print(
            f"[ERROR] get_online_runners(): {e}"
        )

        return None


def get_registration_token():

    """
    Get a temporary GitHub runner registration token.
    """

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
# RUNNER
# ============================================================

def create_runner():

    """
    Create one ephemeral GitHub Actions runner.
    """

    runner_id = uuid.uuid4().hex[:12]

    runner_name = (
        f"deplexo-{runner_id}"
    )

    runner_dir = (
        BASE_DIR / runner_name
    )

    print()
    print("=" * 50)
    print("[RUNNER] Creating new runner")
    print(
        f"[RUNNER] Name: {runner_name}"
    )
    print(
        f"[RUNNER] Repo: {GITHUB_SCOPE}"
    )
    print("=" * 50)

    # --------------------------------------------------------
    # Directory
    # --------------------------------------------------------

    try:

        runner_dir.mkdir(
            parents=True,
            exist_ok=False
        )

    except Exception as e:

        print(
            "[ERROR] Cannot create runner directory: "
            f"{e}"
        )

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
    # Configure runner
    # --------------------------------------------------------

    try:

        config_command = [

            "/home/runner/config.sh",

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
            f"[RUNNER] Registering "
            f"{runner_name}..."
        )

        result = subprocess.run(

            config_command,

            cwd=runner_dir,

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

        print(
            f"[RUNNER] {runner_name} "
            "registered successfully."
        )

    except subprocess.TimeoutExpired:

        print(
            f"[ERROR] Runner registration timeout: "
            f"{runner_name}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    except Exception as e:

        print(
            f"[ERROR] Runner configuration failed: "
            f"{e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    # --------------------------------------------------------
    # Start runner
    # --------------------------------------------------------

    try:

        process = subprocess.Popen(

            [
                "/home/runner/run.sh"
            ],

            cwd=runner_dir,

            stdout=subprocess.PIPE,

            stderr=subprocess.STDOUT,

            text=True,

            bufsize=1,
        )

    except Exception as e:

        print(
            f"[ERROR] Failed to start runner "
            f"{runner_name}: {e}"
        )

        shutil.rmtree(
            runner_dir,
            ignore_errors=True
        )

        return False

    # --------------------------------------------------------
    # Store local runner
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
    # Monitor runner
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

            print()
            print(
                f"[RUNNER] {runner_name} stopped."
            )

            with lock:

                running_runners.pop(
                    runner_name,
                    None
                )

            # Ephemeral runner is normally
            # automatically removed by GitHub
            # after it processes one job.

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            print(
                f"[RUNNER] {runner_name} cleaned."
            )

    thread = threading.Thread(

        target=monitor,

        daemon=True,
    )

    thread.start()

    return True


# ============================================================
# LOCAL RUNNER COUNT
# ============================================================

def local_runner_count():

    with lock:

        return len(
            running_runners
        )


# ============================================================
# RECONCILIATION
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
        f"local processes={local} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # Target already reached
    # --------------------------------------------------------

    if online >= TARGET_RUNNERS:

        print(
            "[MANAGER] Target reached. "
            "No new runners needed."
        )

        return

    # --------------------------------------------------------
    # Calculate missing runners
    # --------------------------------------------------------

    missing = (
        TARGET_RUNNERS - online
    )

    print(
        f"[MANAGER] Missing {missing} "
        "runner(s)."
    )

    # --------------------------------------------------------
    # Create missing runners
    # --------------------------------------------------------

    for i in range(missing):

        if stop_event.is_set():

            break

        print(
            f"[MANAGER] Creating replacement "
            f"{i + 1}/{missing}"
        )

        success = create_runner()

        if not success:

            print(
                "[MANAGER] Runner creation failed."
            )

        # Small delay so GitHub has time
        # to process registrations.

        time.sleep(1)


# ============================================================
# STARTUP
# ============================================================

print()

print("=" * 55)

print(
    "       DEPLEXO GITHUB RUNNER MANAGER"
)

print("=" * 55)

print(
    f"Target repo : {GITHUB_SCOPE}"
)

print(
    f"Target      : {TARGET_RUNNERS} runners"
)

print(
    f"Interval    : {CHECK_INTERVAL}s"
)

print(
    f"Labels      : {RUNNER_LABELS}"
)

print(
    f"Runner dir  : {BASE_DIR}"
)

print("=" * 55)

print()


# ============================================================
# INITIAL RECONCILIATION
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

        print("=" * 55)

        print(
            "[MANAGER] Checking runner count..."
        )

        print("=" * 55)

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

    process = runner["process"]

    try:

        process.terminate()

    except Exception:

        pass


print(
    "[MANAGER] Shutdown complete."
)
