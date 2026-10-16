FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd -m bot && mkdir -p /app/config && chown -R bot /app
USER bot

EXPOSE 8000 8501 9100
CMD ["python", "main.py"]
