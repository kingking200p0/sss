#!/bin/bash

set -u

if [ -z "${GITHUB_PAT:-}" ]; then
    echo "ERROR: GITHUB_PAT is not set."
    exit 1
fi

if [ -z "${GITHUB_SCOPE:-}" ]; then
    echo "ERROR: GITHUB_SCOPE is not set."
    exit 1
fi

REPO_URL="https://github.com/${GITHUB_SCOPE}"
API_URL="https://api.github.com/repos/${GITHUB_SCOPE}/actions/runners/registration-token"

echo "=========================================="
echo " GitHub Actions Self-Hosted Runner"
echo " Repository: ${GITHUB_SCOPE}"
echo "=========================================="

cleanup() {
    echo "Stopping runner..."

    if [ -n "${RUNNER_CONFIGURED:-}" ]; then
        ./config.sh remove --token "${REMOVE_TOKEN:-}" 2>/dev/null || true
    fi

    exit 0
}

trap cleanup SIGTERM SIGINT

while true; do

    echo
    echo "=== Requesting GitHub registration token ==="

    RESPONSE=$(curl -fsS \
        -X POST \
        -H "Authorization: Bearer ${GITHUB_PAT}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${API_URL}" 2>/tmp/github_error)

    CURL_STATUS=$?

    if [ "$CURL_STATUS" -ne 0 ]; then
        echo "ERROR: GitHub API request failed."

        if [ -f /tmp/github_error ]; then
            cat /tmp/github_error
        fi

        echo "Retrying in 30 seconds..."
        sleep 30
        continue
    fi

    REG_TOKEN=$(echo "$RESPONSE" | jq -r '.token // empty')

    if [ -z "$REG_TOKEN" ]; then
        echo "ERROR: GitHub did not return a registration token."
        echo "Response:"
        echo "$RESPONSE"
        echo
        echo "Retrying in 30 seconds..."
        sleep 30
        continue
    fi

    # Unique runner name
    RANDOM_ID=$(cat /proc/sys/kernel/random/uuid | cut -d- -f1)

    RUNNER_NAME="deplexo-${RANDOM_ID}-$(date +%s)"

    echo
    echo "=== Configuring runner ==="
    echo "Runner name: ${RUNNER_NAME}"

    # Clean previous configuration
    if [ -f ".runner" ]; then
        echo "Removing previous runner configuration..."
        ./config.sh remove --token "${REG_TOKEN}" 2>/dev/null || true
        rm -rf .runner .credentials .credentials_rsaparams
    fi

    ./config.sh \
        --url "${REPO_URL}" \
        --token "${REG_TOKEN}" \
        --name "${RUNNER_NAME}" \
        --labels "self-hosted,linux,x64,deplexo" \
        --work "_work" \
        --ephemeral \
        --unattended

    CONFIG_STATUS=$?

    if [ "$CONFIG_STATUS" -ne 0 ]; then
        echo "ERROR: Runner configuration failed."
        sleep 30
        continue
    fi

    RUNNER_CONFIGURED=1
    REMOVE_TOKEN="${REG_TOKEN}"

    echo
    echo "=========================================="
    echo " Runner is ONLINE"
    echo " Name: ${RUNNER_NAME}"
    echo " Labels: self-hosted,linux,x64,deplexo"
    echo "=========================================="
    echo

    ./run.sh

    RUN_STATUS=$?

    echo
    echo "Runner stopped with exit code: ${RUN_STATUS}"

    # Ephemeral runner should only execute one job.
    # Remove local runner configuration.
    echo "Cleaning runner..."

    ./config.sh remove --token "${REG_TOKEN}" 2>/dev/null || true

    rm -rf \
        .runner \
        .credentials \
        .credentials_rsaparams \
        _work

    RUNNER_CONFIGURED=

    echo
    echo "=== Starting a new ephemeral runner ==="
    echo "Waiting 5 seconds..."
    sleep 5

done
