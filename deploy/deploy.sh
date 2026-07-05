#!/bin/bash
# ============================================================
# LifeVault — Google Cloud Run Deployment Script
# ============================================================
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - A GCP project with billing enabled
#   - Cloud Run API enabled
#
# Usage:
#   ./deploy/deploy.sh <PROJECT_ID> <GOOGLE_API_KEY>
# ============================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: ./deploy/deploy.sh <PROJECT_ID> <GOOGLE_API_KEY>}"
API_KEY="${2:?Usage: ./deploy/deploy.sh <PROJECT_ID> <GOOGLE_API_KEY>}"
REGION="us-central1"
SERVICE_NAME="lifevault"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "=== LifeVault Cloud Run Deployment ==="
echo "Project:  ${PROJECT_ID}"
echo "Region:   ${REGION}"
echo "Image:    ${IMAGE}"
echo ""

# Step 1: Set the project
echo ">>> Setting GCP project..."
gcloud config set project "${PROJECT_ID}"

# Step 2: Enable required APIs
echo ">>> Enabling Cloud Run and Container Registry APIs..."
gcloud services enable run.googleapis.com containerregistry.googleapis.com

# Step 3: Build and push the Docker image
echo ">>> Building and pushing Docker image..."
gcloud builds submit --tag "${IMAGE}" .

# Step 4: Create the API key secret (if it doesn't exist)
echo ">>> Storing API key in Secret Manager..."
echo -n "${API_KEY}" | gcloud secrets create gemini-api-key \
    --replication-policy="automatic" \
    --data-file=- 2>/dev/null || \
echo -n "${API_KEY}" | gcloud secrets versions add gemini-api-key --data-file=-

# Step 5: Grant Cloud Run access to the secret
echo ">>> Granting secret access..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

# Step 6: Deploy to Cloud Run
echo ">>> Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE}" \
    --region "${REGION}" \
    --platform managed \
    --allow-unauthenticated \
    --port 8000 \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 3 \
    --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
    --set-env-vars "GOOGLE_GENAI_MODEL=gemini-2.5-flash,GOOGLE_GENAI_USE_VERTEXAI=FALSE,VAULT_DB_PATH=/app/vault_data/vault.db"

# Step 7: Get the URL
echo ""
echo "=== Deployment Complete ==="
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format="value(status.url)")
echo "LifeVault is live at: ${SERVICE_URL}"
echo ""
echo "Test it:"
echo "  open ${SERVICE_URL}"
