FROM ghcr.io/actions/actions-runner:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-requests \
        ca-certificates \
        curl \
        jq \
        coreutils \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
# Immutable runner source
# ------------------------------------------------------------

RUN mkdir -p /opt/runner-source \
    && cp -a /home/runner/. /opt/runner-source/ \
    && chown -R runner:runner /opt/runner-source \
    && chmod -R a+rX /opt/runner-source

# ------------------------------------------------------------
# Manager
# ------------------------------------------------------------

COPY manager.py /manager.py

RUN chmod 0755 /manager.py \
    && chown runner:runner /manager.py

USER runner

ENV PYTHONUNBUFFERED=1

# Deplexo writable area
ENV RUNNER_MANAGER_DIR=/tmp/runner-manager

WORKDIR /tmp

ENTRYPOINT ["python3", "/manager.py"]
