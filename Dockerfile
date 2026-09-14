FROM ghcr.io/actions/actions-runner:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh

RUN chmod +x /home/runner/entrypoint.sh

USER runner

WORKDIR /home/runner

ENTRYPOINT ["/home/runner/entrypoint.sh"]
