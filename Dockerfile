FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Europe/Vienna
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tradingbot ./tradingbot
COPY main.py .
CMD ["python", "main.py", "run"]
