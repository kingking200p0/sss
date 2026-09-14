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
        rsync \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
# Runner source
# ------------------------------------------------------------
# The original /home/runner installation is part of the image
# and is never used as a writable runner instance.
#
# We keep it as a source tree and create the writable runtime
# tree at startup.
# ------------------------------------------------------------

RUN mkdir -p /opt/runner-source \
    && cp -a /home/runner/. /opt/runner-source/ \
    && chmod -R a+rX /opt/runner-source

# ------------------------------------------------------------
# Writable manager directory
# ------------------------------------------------------------

RUN mkdir -p /runner-manager \
    && chown -R runner:runner /runner-manager

COPY manager.py /manager.py

RUN chmod +x /manager.py \
    && chown runner:runner /manager.py

# ------------------------------------------------------------
# Run as non-root
# ------------------------------------------------------------

USER runner

WORKDIR /runner-manager

ENV PYTHONUNBUFFERED=1
ENV RUNNER_MANAGER_DIR=/runner-manager

ENTRYPOINT ["python3", "/manager.py"]
