#!/usr/bin/env bash
# Deploy the MCP server to Cloud Run.
#
# Environment variables are set on the first deploy only; later runs reuse
# whatever the service already has. PUBLIC_BASE_URL is the exception worth
# watching: it must match the service's own URL, and that URL is not known
# until after the first deploy (see README).
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:-oakmega-take-home}"
REGION="${GCP_REGION:-asia-east1}"
SERVICE="${CLOUD_RUN_SERVICE:-oakmega-drive-mcp}"
GCLOUD="${GCLOUD_BIN:-gcloud}"

cd "$(dirname "$0")/../server"

"$GCLOUD" run deploy "$SERVICE" \
  --source=. \
  --region="$REGION" \
  --project="$PROJECT" \
  --allow-unauthenticated \
  --quiet

URL=$("$GCLOUD" run services describe "$SERVICE" \
        --region="$REGION" --project="$PROJECT" --format="value(status.url)")

echo
echo "Deployed: $URL"
echo "Health:   $(curl -s "$URL/status" || echo 'no response')"
