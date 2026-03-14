FROM selenium/standalone-chrome:latest

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/venv/bin:$PATH"
ENV PYTHONPATH="/app:/app/libs:/app/services"

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    vim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /app/venv

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN /app/venv/bin/pip install --upgrade pip && \
    /app/venv/bin/pip install -r requirements.txt

COPY libs/ ./libs/
COPY services/ ./services/
COPY doc/ ./doc/
COPY README.md ./README.md
COPY .env.example ./.env.example

EXPOSE 8000 8001 8002 8003 8004

CMD ["uvicorn", "services.orchestrator_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
