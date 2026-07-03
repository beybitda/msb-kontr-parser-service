FROM python:3.11-slim

WORKDIR /app

# oracledb в thin-режиме не требует Instant Client, но если понадобится thick-режим —
# добавить libaio1 и Oracle Instant Client сюда.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
