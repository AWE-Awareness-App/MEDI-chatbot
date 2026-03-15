FROM python:3.11-slim

WORKDIR /app

# Background voice processing now runs inside API container.
# ffmpeg is required by STT audio conversion.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# chatterbox deps (pkuseg) require numpy import during setup.py metadata phase
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir numpy==1.25.2 \
 && pip install --no-cache-dir -r requirements.txt

# Copy only application code - secrets/PDFs come in via env vars + volumes at runtime
COPY app/ ./app/

EXPOSE 8000

# 2 workers: matches P1v3 vCore count; increase if scaling up the App Service plan
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
