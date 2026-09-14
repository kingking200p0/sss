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
GITHUB_SCOPE = os.environ.get("GITHUB_SCOPE", "kingking0020/mmm")

TARGET_RUNNERS = int(os.environ.get("TARGET_RUNNERS", "15"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

RUNNER_LABELS = os.environ.get(
    "RUNNER_LABELS",
    "self-hosted,linux,x64,deplexo"
)

BASE_DIR = Path("/runner-manager/runners")

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


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


# ============================================================
# VALIDATION
# ============================================================

if not GITHUB_TOKEN:
    print("[ERROR] GG_TOKEN is not set.")
    raise SystemExit(1)


BASE_DIR.mkdir(parents=True, exist_ok=True)


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
    Returns the number of online runners in the target repository.
    """

    try:
        response = requests.get(
            RUNNERS_URL,
            headers=github_headers(),
            params={
                "per_page": 100,
            },
            timeout=20,
        )

        if response.status_code != 200:
            print(
                f"[ERROR] Failed to list runners: "
                f"{response.status_code} {response.text}"
            )
            return None

        data = response.json()

        runners = data.get("runners", [])

        online = [
            runner
            for runner in runners
            if runner.get("status") == "online"
        ]

        print(
            f"[GITHUB] Total registered: {len(runners)} | "
            f"Online: {len(online)}"
        )

        return len(online)

    except Exception as e:
        print(f"[ERROR] get_online_runners(): {e}")
        return None


def get_registration_token():
    """
    Gets a temporary registration token from GitHub.

    The token is valid for one hour.
    """

    try:
        response = requests.post(
            REGISTRATION_TOKEN_URL,
            headers=github_headers(),
            timeout=20,
        )

        if response.status_code not in (200, 201):
            print(
                f"[ERROR] Registration token failed: "
                f"{response.status_code} {response.text}"
            )
            return None

        data = response.json()

        token = data.get("token")

        if not token:
            print("[ERROR] GitHub returned no registration token.")
            return None

        return token

    except Exception as e:
        print(f"[ERROR] get_registration_token(): {e}")
        return None


# ============================================================
# RUNNER
# ============================================================

def create_runner():
    """
    Creates one ephemeral GitHub runner.
    """

    runner_id = uuid.uuid4().hex[:12]

    runner_name = f"deplexo-{runner_id}"

    runner_dir = BASE_DIR / runner_name

    print()
    print("==========================================")
    print("[RUNNER] Creating new runner")
    print(f"[RUNNER] Name: {runner_name}")
    print(f"[RUNNER] Repo: {GITHUB_SCOPE}")
    print("==========================================")

    try:
        runner_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        print(f"[ERROR] Cannot create runner directory: {e}")
        return

    token = get_registration_token()

    if not token:
        shutil.rmtree(runner_dir, ignore_errors=True)
        return

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

        print(f"[RUNNER] Registering {runner_name}...")

        result = subprocess.run(
            config_command,
            cwd=runner_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        print(result.stdout)

        if result.returncode != 0:
            print(
                f"[ERROR] Runner registration failed: "
                f"{runner_name}"
            )

            shutil.rmtree(
                runner_dir,
                ignore_errors=True
            )

            return

        print(
            f"[RUNNER] {runner_name} registered successfully."
        )

        # ----------------------------------------------------
        # Start runner
        # ----------------------------------------------------

        process = subprocess.Popen(
            ["/home/runner/run.sh"],
            cwd=runner_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with lock:
            running_runners[runner_name] = {
                "process": process,
                "directory": runner_dir,
            }

        # ----------------------------------------------------
        # Read runner output
        # ----------------------------------------------------

        def monitor():

            try:

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

                with lock:
                    running_runners.pop(
                        runner_name,
                        None
                    )

                # Ephemeral runner should have already been
                # removed by GitHub after its job.

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

    except Exception as e:

        print(
            f"[ERROR] Failed to start runner "
            f"{runner_name}: {e}"
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


# ============================================================
# RECONCILIATION
# ============================================================

def local_runner_count():

    with lock:
        return len(running_runners)


def ensure_target_count():

    online = get_online_runners()

    if online is None:
        print(
            "[MANAGER] Could not determine GitHub runner count."
        )
        return

    local = local_runner_count()

    print(
        f"[MANAGER] GitHub online={online} | "
        f"local processes={local} | "
        f"target={TARGET_RUNNERS}"
    )

    # --------------------------------------------------------
    # Important:
    #
    # We only create enough runners to compensate for the
    # online count.
    # --------------------------------------------------------

    missing = TARGET_RUNNERS - online

    if missing <= 0:

        print(
            "[MANAGER] Target reached. "
            "No new runners needed."
        )

        return

    print(
        f"[MANAGER] Missing {missing} runner(s). "
        f"Creating replacement runner(s)..."
    )

    # Don't blindly create more than our local capacity.
    # This prevents duplicate creation while GitHub's API
    # takes a few seconds to show newly registered runners.

    capacity = TARGET_RUNNERS - local

    if capacity <= 0:

        print(
            "[MANAGER] Local target already reached. "
            "Waiting for GitHub to update."
        )

        return

    create_count = min(
        missing,
        capacity
    )

    print(
        f"[MANAGER] Creating {create_count} runner(s)."
    )

    for _ in range(create_count):

        if stop_event.is_set():
            break

        create_runner()

        # Give GitHub a moment between registrations.
        time.sleep(1)


# ============================================================
# STARTUP
# ============================================================

print()
print("==========================================")
print("      DEPLEXO GITHUB RUNNER MANAGER")
print("==========================================")
print(f"Target repo : {GITHUB_SCOPE}")
print(f"Target      : {TARGET_RUNNERS} runners")
print(f"Interval    : {CHECK_INTERVAL}s")
print(f"Labels      : {RUNNER_LABELS}")
print("==========================================")
print()


# ============================================================
# INITIAL 15
# ============================================================

print(
    "[MANAGER] Initial startup: "
    f"creating {TARGET_RUNNERS} runners..."
)

for i in range(TARGET_RUNNERS):

    if stop_event.is_set():
        break

    print(
        f"[MANAGER] Initial runner "
        f"{i + 1}/{TARGET_RUNNERS}"
    )

    create_runner()

    time.sleep(1)


# ============================================================
# MAIN LOOP
# ============================================================

while not stop_event.is_set():

    try:

        print()
        print("==========================================")
        print("[MANAGER] Checking runner count...")
        print("==========================================")

        ensure_target_count()

    except Exception as e:

        print(
            f"[ERROR] Main loop error: {e}"
        )

    # --------------------------------------------------------
    # Wait
    # --------------------------------------------------------

    stop_event.wait(CHECK_INTERVAL)


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
