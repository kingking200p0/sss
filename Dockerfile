FROM ghcr.io/actions/actions-runner:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-requests \
        ca-certificates \
        curl \
        jq \
        tar \
    && rm -rf /var/lib/apt/lists/*

# Create an immutable archive of the COMPLETE runner installation.
RUN cd /home/runner \
    && tar -czf /opt/actions-runner.tar.gz . \
    && chmod 0644 /opt/actions-runner.tar.gz

COPY manager.py /manager.py

RUN chmod 0755 /manager.py

USER runner

ENV PYTHONUNBUFFERED=1
ENV RUNNER_ARCHIVE=/opt/actions-runner.tar.gz
ENV RUNNER_MANAGER_DIR=/tmp/runner-manager

WORKDIR /tmp

ENTRYPOINT ["python3", "/manager.py"]
