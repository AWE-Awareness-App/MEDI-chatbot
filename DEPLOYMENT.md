# MEDI Chatbot — Azure Deployment Guide

## Azure Resources

| Resource | Value |
|---|---|
| Resource Group | `awe-web` |
| App Service Plan | `ASP-aweweb-8563` (Linux, P1v3, Canada Central) |
| Container Registry | `aweacrconnection.azurecr.io` |
| Web App Name | `medi-chatbot` |
| Docker Image | `aweacrconnection.azurecr.io/medi-chatbot:main-latest` |
| App URL | `https://medi-chatbot.azurewebsites.net` |

---

## Step 1 — Create the Azure Web App

In the Azure Portal:

1. **Create a resource → Web App**
2. Fill in:
   - Subscription: `Azure subscription 1`
   - Resource Group: `awe-web`
   - Name: `medi-chatbot`
   - Publish: `Container`
   - OS: `Linux`
   - Region: `Canada Central`
   - App Service Plan: `ASP-aweweb-8563`
3. Under **Container**:
   - Image source: `Azure Container Registry`
   - Registry: `aweacrconnection`
   - Image: `medi-chatbot`
   - Tag: `main-latest`
4. Click **Review + create**

---

## Step 2 — Create Azure PostgreSQL Flexible Server (with pgvector)

MEDI requires PostgreSQL with the `pgvector` extension enabled.

1. Create an **Azure Database for PostgreSQL Flexible Server** in resource group `awe-web`
2. Go to **Server parameters** and enable `VECTOR` under `azure.extensions`
3. Connect to the DB and run once:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id         SERIAL PRIMARY KEY,
    content    TEXT         NOT NULL,
    topic      TEXT,
    source     TEXT,
    metadata   JSONB,
    chunk_hash TEXT         UNIQUE NOT NULL,
    embedding  vector(1536)
);
```

4. Copy the connection string — you will need it for `DATABASE_URL` below

---

## Step 3 — Set Environment Variables in Azure Portal

Go to: **medi-chatbot → Settings → Environment variables → Add**

### Required

| Key | Value |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://user:password@host:5432/dbname` |
| `OPENAI_API_KEY` | your OpenAI key |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` |
| `WEBSITES_PORT` | `8000` |

### App Behaviour

| Key | Value |
|---|---|
| `APP_NAME` | `MEDI` |
| `ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `LLM_PROVIDER` | `anthropic` |
| `USE_LLM` | `true` |
| `LLM_MAX_HISTORY` | `12` |
| `RAG_TOP_K` | `5` |
| `DEBUG_RAG` | `false` |

### Twilio (WhatsApp)

| Key | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | your Twilio Auth Token |
| `TWILIO_WHATSAPP_NUMBER` | `whatsapp:+14155238886` |
| `MENU_TEMPLATE_SID` | your `HX...` template SID |

---

## Step 4 — Run Knowledge Base Ingestion (one-time)

The `knowledge_chunks` table must be populated before the chatbot can answer with citations.
Run this once, pointed at the Azure database:

```bash
# Option A: locally with the Azure DB URL
DATABASE_URL="postgresql+psycopg2://user:password@host:5432/dbname" \
  python app/scripts/ingest_knowledge.py

# Option B: via Docker with the Azure DB URL
DATABASE_URL="postgresql+psycopg2://user:password@host:5432/dbname" \
  docker compose --profile ingest run --rm ingest
```

Re-running is safe — SHA256 deduplication skips any chunk already in the DB.

---

## Step 5 — Connect Azure DevOps Pipeline

1. In Azure DevOps, create a new pipeline pointing to `azure-pipelines.yml` in this repo
2. Verify these service connections exist in your project settings:
   - **aweacrconnection** — Azure Container Registry connection
   - **Azure subscription 1** — Azure Resource Manager connection
3. Run the pipeline manually once to confirm the first build and deploy

---

## Step 6 — Set Twilio Webhook URL

In the Twilio Console, set the WhatsApp sandbox (or number) webhook to:

```
https://medi-chatbot.azurewebsites.net/webhook/twilio
```

Method: **HTTP POST**

---

## Pipeline Summary

**File**: `azure-pipelines.yml`

| Setting | Value |
|---|---|
| Trigger branch | `main` |
| ACR service connection | `aweacrconnection` |
| Azure subscription connection | `Azure subscription 1` |
| Image tag | `main-latest` |
| Image pushed | `aweacrconnection.azurecr.io/medi-chatbot:main-latest` |
| Web App deployed | `medi-chatbot` |

Every push to `main` automatically builds the Docker image, pushes it to ACR, and redeploys the Web App.

---

## docker-compose.yml and Azure

`docker-compose.yml` is **local development only**. Azure App Service pulls and runs the Docker image
directly — it does not use docker-compose. There is no conflict between the two.
