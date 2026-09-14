import os
import time
import requests


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.environ["GG_TOKEN"]

OWNER = os.environ.get(
    "GITHUB_OWNER",
    "kingking0020",
)

REPO = os.environ.get(
    "GITHUB_REPO",
    "mmm",
)

WORKFLOW = os.environ.get(
    "WORKFLOW",
    "machine.yml",
)

BRANCH = os.environ.get(
    "BRANCH",
    "main",
)

TARGET = int(
    os.environ.get(
        "TARGET",
        "15",
    )
)

CHECK_INTERVAL = int(
    os.environ.get(
        "CHECK_INTERVAL",
        "30",
    )
)


# ============================================================
# GITHUB API
# ============================================================

API = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# ACTIVE STATUSES
# ============================================================

ACTIVE_STATUSES = {
    "queued",
    "in_progress",
    "requested",
    "waiting",
    "pending",
}


# ============================================================
# GET ACTIVE WORKFLOW RUNS
# ============================================================

def get_active_runs():
    url = (
        f"{API}/repos/"
        f"{OWNER}/{REPO}/actions/workflows/"
        f"{WORKFLOW}/runs"
    )

    try:
        response = session.get(
            url,
            params={
                "per_page": 100,
            },
            timeout=20,
        )

        if response.status_code != 200:
            print(
                f"[ERROR] Cannot get workflow runs: "
                f"HTTP {response.status_code}",
                flush=True,
            )
            print(
                response.text[:500],
                flush=True,
            )
            return None

        data = response.json()

        runs = data.get(
            "workflow_runs",
            [],
        )

        active = [
            run
            for run in runs
            if run.get("status") in ACTIVE_STATUSES
        ]

        print(
            f"[GITHUB] "
            f"Workflow={WORKFLOW} | "
            f"Active={len(active)}",
            flush=True,
        )

        return len(active)

    except Exception as e:
        print(
            f"[ERROR] get_active_runs(): {e}",
            flush=True,
        )
        return None


# ============================================================
# DISPATCH ONE WORKFLOW
# ============================================================

def dispatch_one(number):
    url = (
        f"{API}/repos/"
        f"{OWNER}/{REPO}/actions/workflows/"
        f"{WORKFLOW}/dispatches"
    )

    payload = {
        "ref": BRANCH,
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=20,
        )

        if response.status_code in (200, 204):
            print(
                f"[START] Machine {number} dispatched",
                flush=True,
            )
            return True

        print(
            f"[ERROR] Machine {number} failed: "
            f"HTTP {response.status_code}",
            flush=True,
        )
        print(
            response.text[:500],
            flush=True,
        )

        return False

    except Exception as e:
        print(
            f"[ERROR] dispatch_one(): {e}",
            flush=True,
        )
        return False


# ============================================================
# START EXACTLY N MACHINES
# ============================================================

def start_machines(count):
    if count <= 0:
        return

    print(
        f"[MANAGER] Starting {count} machine(s)...",
        flush=True,
    )

    success = 0

    for i in range(1, count + 1):
        if dispatch_one(i):
            success += 1

        # Avoid sending all requests at exactly the same time.
        time.sleep(1)

    print(
        f"[MANAGER] Started "
        f"{success}/{count}",
        flush=True,
    )


# ============================================================
# INITIAL START
# ============================================================

def initial_start():
    print(
        "[MANAGER] Initial startup: "
        f"starting {TARGET} machines...",
        flush=True,
    )

    start_machines(TARGET)


# ============================================================
# REPLENISH
# ============================================================

def replenish():
    active = get_active_runs()

    if active is None:
        print(
            "[MANAGER] Could not determine "
            "active machine count.",
            flush=True,
        )
        return

    print(
        f"[MANAGER] Active={active}/{TARGET}",
        flush=True,
    )

    if active >= TARGET:
        print(
            "[MANAGER] Target reached. "
            "Nothing to do.",
            flush=True,
        )
        return

    missing = TARGET - active

    print(
        f"[MANAGER] {missing} machine(s) missing.",
        flush=True,
    )

    start_machines(missing)


# ============================================================
# MAIN
# ============================================================

print(
    "==========================================",
    flush=True,
)

print(
    "       GITHUB MACHINE MANAGER",
    flush=True,
)

print(
    "==========================================",
    flush=True,
)

print(
    f"Repository : {OWNER}/{REPO}",
    flush=True,
)

print(
    f"Workflow   : {WORKFLOW}",
    flush=True,
)

print(
    f"Branch     : {BRANCH}",
    flush=True,
)

print(
    f"Target     : {TARGET}",
    flush=True,
)

print(
    f"Check      : {CHECK_INTERVAL}s",
    flush=True,
)

print(
    "==========================================",
    flush=True,
)


# ============================================================
# STEP 1
# FIRST START 15
# ============================================================

initial_start()


# ============================================================
# STEP 2
# CHECK EVERY 30 SECONDS
# ============================================================

while True:
    print(
        "\n[MANAGER] Waiting "
        f"{CHECK_INTERVAL}s...",
        flush=True,
    )

    time.sleep(
        CHECK_INTERVAL
    )

    print(
        "\n==========================================",
        flush=True,
    )

    print(
        "[MANAGER] Periodic check",
        flush=True,
    )

    print(
        "==========================================",
        flush=True,
    )

    replenish()
