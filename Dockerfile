FROM ghcr.io/actions/actions-runner:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-requests \
        ca-certificates \
        curl \
        jq \
    && rm -rf /var/lib/apt/lists/*

# Keep the official runner as an immutable source.
RUN mkdir -p /opt/runner-source \
    && cp -a /home/runner/. /opt/runner-source/ \
    && chown -R runner:runner /opt/runner-source \
    && chmod -R a+rX /opt/runner-source

COPY manager.py /manager.py

RUN chmod +x /manager.py \
    && chown runner:runner /manager.py

USER runner

ENV RUNNER_MANAGER_DIR=/tmp/runner-manager
ENV PYTHONUNBUFFERED=1

WORKDIR /tmp

ENTRYPOINT ["python3", "/manager.py"]
