FROM selenium/standalone-chrome:latest

WORKDIR /app

COPY requirements.txt .

USER root
RUN apt-get update && apt-get install -y python3-venv python3-pip
RUN python3 -m venv venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install -r requirements.txt
USER seluser

COPY . .

EXPOSE 5555

CMD ["python3", "loginlocal.py"]