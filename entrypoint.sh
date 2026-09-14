#!/bin/bash
set -e

if [ -z "$GITHUB_PAT" ] || [ -z "$GITHUB_SCOPE" ]; then
  echo "ERROR: GITHUB_PAT and GITHUB_SCOPE must be set."
  exit 1
fi

while true; do
  echo "=== Getting registration token ==="

  API_URL="https://api.github.com/repos/${GITHUB_SCOPE}/actions/runners/registration-token"

  REG_TOKEN=$(curl -s -X POST \
    -H "Authorization: token ${GITHUB_PAT}" \
    -H "Accept: application/vnd.github.v3+json" \
    "${API_URL}" | jq -r .token)

  if [ -z "$REG_TOKEN" ] || [ "$REG_TOKEN" = "null" ]; then
    echo "ERROR: Failed to get token. Retrying in 30s..."
    sleep 30
    continue
  fi

  RUNNER_NAME="railway-${RAILWAY_REPLICA_ID:-$RANDOM}-$(date +%s)"
  echo "=== Registering runner: ${RUNNER_NAME} ==="

  ./config.sh --url "https://github.com/${GITHUB_SCOPE}" \
    --token "${REG_TOKEN}" \
    --ephemeral \
    --unattended \
    --name "${RUNNER_NAME}" \
    --labels self-hosted,linux,x64,railway \
    --work _work

  echo "=== Runner started, waiting for job ==="
  ./run.sh || true

  echo "=== Job done, starting a new runner ==="
  ./config.sh remove --token "${REG_TOKEN}" 2>/dev/null || true
  rm -rf _work
done
