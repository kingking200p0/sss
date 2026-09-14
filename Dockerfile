FROM python:3.12-slim

RUN pip install --no-cache-dir requests

COPY manager.py /manager.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "/manager.py"]
