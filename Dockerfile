FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only application code — secrets/PDFs come in via env vars + volumes at runtime
COPY app/ ./app/

EXPOSE 8000

# 2 workers: matches P1v3 vCore count; increase if scaling up the App Service plan
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
