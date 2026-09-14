FROM ghcr.io/actions/actions-runner:latest

USER root
RUN apt-get update && apt-get install -y curl jq && rm -rf /var/lib/apt/lists/*
USER runner

COPY --chown=runner:docker entrypoint.sh /home/runner/entrypoint.sh
RUN chmod +x /home/runner/entrypoint.sh

ENTRYPOINT ["/home/runner/entrypoint.sh"]
