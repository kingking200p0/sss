FROM ghcr.io/actions/actions-runner:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       jq \
       python3 \
       python3-requests \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY manager.py /manager.py

RUN chmod +x /manager.py \
    && mkdir -p /runner-manager \
    && chown -R runner:runner /runner-manager

USER runner

WORKDIR /runner-manager

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/manager.py"]
