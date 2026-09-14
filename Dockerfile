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

RUN mkdir -p /opt/runner-source \
    && cp -a /home/runner/. /opt/runner-source/ \
    && chown -R runner:runner /opt/runner-source

COPY manager.py /manager.py

RUN chmod 755 /manager.py \
    && chown runner:runner /manager.py

USER runner

ENV PYTHONUNBUFFERED=1
ENV PORT=3000

WORKDIR /tmp

ENTRYPOINT ["python3", "/manager.py"]
