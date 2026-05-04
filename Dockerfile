FROM python:3.12-slim

# nsenter lives in util-linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cpu_service.py .

EXPOSE 5001

CMD ["python", "cpu_service.py"]
