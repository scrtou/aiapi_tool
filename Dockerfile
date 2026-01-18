# Use the Selenium standalone Chrome image as base
FROM selenium/standalone-chrome:latest

# Switch to root for installations
USER root

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/venv/bin:$PATH"
ENV PYTHONPATH="/app:/app/src"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    vim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment
RUN python3 -m venv /app/venv
# Upgrade pip and install Python dependencies
RUN /app/venv/bin/pip install --upgrade pip && \
    /app/venv/bin/pip install \
    selenium \
    webdriver-manager \
    fastapi \
    uvicorn \
    pydantic \
    requests \
    httpx \
    psutil

# Set work directory
WORKDIR /app

# Copy application code
COPY src/ ./src/

# Expose port
EXPOSE 5557

# Run the application using venv python with unbuffered output
CMD ["python", "-u", "src/loginlocal.py"]