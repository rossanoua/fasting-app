FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py db.py ./

# SQLite db lives in /app/data (mounted volume for persistence)
RUN mkdir -p /app/data

CMD ["python", "-u", "bot.py"]
