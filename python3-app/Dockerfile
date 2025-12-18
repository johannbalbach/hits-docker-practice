FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# системные пакеты (минимум)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*

# зависимости
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# код
COPY . /app

EXPOSE 8888

# по умолчанию: app.py или main.py
# если у вас файл называется main.py — замените команду
CMD ["python", "main.py"]