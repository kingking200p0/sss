import os
import time
import requests

GG_TOKEN = os.environ["GG_TOKEN"]

OWNER = "kingking0020"
REPO = "mmm"
WORKFLOW = "screenshot.yml"
BRANCH = "main"

CHECK_INTERVAL = 40

API = "https://api.github.com"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GG_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}


def dispatch_workflow():
    url = f"{API}/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/dispatches"

    response = requests.post(
        url,
        headers=HEADERS,
        json={"ref": BRANCH},
        timeout=30,
    )

    if response.status_code not in (200, 201, 204):
        print(f"[ERROR] Dispatch failed: HTTP {response.status_code}")
        print(response.text)
        return False

    print("[START] Workflow dispatched.")
    return True


def get_latest_run():
    url = f"{API}/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/runs"

    response = requests.get(
        url,
        headers=HEADERS,
        params={
            "branch": BRANCH,
            "per_page": 1,
        },
        timeout=30,
    )

    if response.status_code != 200:
        print(f"[ERROR] Cannot get workflow runs: HTTP {response.status_code}")
        print(response.text)
        return None

    runs = response.json().get("workflow_runs", [])

    if not runs:
        return None

    return runs[0]


def wait_for_new_run(previous_id):
    while True:
        run = get_latest_run()

        if run and run["id"] != previous_id:
            return run

        time.sleep(2)


def get_run(run_id):
    url = f"{API}/repos/{OWNER}/{REPO}/actions/runs/{run_id}"

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    if response.status_code != 200:
        print(
            f"[ERROR] Cannot get Run {run_id}: "
            f"HTTP {response.status_code}"
        )
        print(response.text)
        return None

    return response.json()


def main():
    print("=" * 60)
    print("       SINGLE GITHUB ACTION MANAGER")
    print("=" * 60)
    print(f"Repository : {OWNER}/{REPO}")
    print(f"Workflow   : {WORKFLOW}")
    print(f"Branch     : {BRANCH}")
    print(f"Check      : every {CHECK_INTERVAL}s")
    print("=" * 60)

    previous_run_id = None

    while True:

        # 1. Start ONE workflow
        print("[MANAGER] Starting one workflow...")

        if not dispatch_workflow():
            print("[MANAGER] Dispatch failed. Retrying in 10s...")
            time.sleep(10)
            continue

        # 2. Find exactly the newly created Run
        new_run = wait_for_new_run(previous_run_id)
        run_id = new_run["id"]

        previous_run_id = run_id

        print(f"[MANAGER] Tracking Run: {run_id}")

        # 3. Check this Run every 40 seconds
        while True:

            run = get_run(run_id)

            if run is None:
                print(
                    f"[MANAGER] Status unavailable. "
                    f"Retrying in {CHECK_INTERVAL}s..."
                )
                time.sleep(CHECK_INTERVAL)
                continue

            status = run.get("status")
            conclusion = run.get("conclusion")

            print(
                f"[CHECK] Run {run_id} | "
                f"status={status} | "
                f"conclusion={conclusion}"
            )

            # Still running/queued
            if status != "completed":
                print(
                    f"[MANAGER] Run still active. "
                    f"Next check in {CHECK_INTERVAL}s..."
                )
                time.sleep(CHECK_INTERVAL)
                continue

            # 4. Run finished
            print(
                f"[MANAGER] Run {run_id} completed "
                f"with result: {conclusion}"
            )

            # 5. Break -> outer loop starts exactly ONE new workflow
            break


if __name__ == "__main__":
    main()
