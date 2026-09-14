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
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------
# Keep the official runner installation as a READ-ONLY source.
#
# Deplexo may mount the container filesystem read-only at runtime,
# so no runtime files will ever be created here.
# ------------------------------------------------------------

RUN mkdir -p /opt/runner-source \
    && cp -a /home/runner/. /opt/runner-source/ \
    && chown -R runner:runner /opt/runner-source \
    && chmod -R a+rX /opt/runner-source

# ------------------------------------------------------------
# Manager
# ------------------------------------------------------------

COPY manager.py /manager.py

RUN chmod +x /manager.py \
    && chown runner:runner /manager.py

USER runner

# IMPORTANT:
# Runtime writable directory is /tmp.
ENV RUNNER_MANAGER_DIR=/tmp/runner-manager
ENV PYTHONUNBUFFERED=1

WORKDIR /tmp

ENTRYPOINT ["python3", "/manager.py"]
