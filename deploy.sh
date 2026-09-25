#!/usr/bin/env bash
set -euo pipefail

# Deploys showcase-ibb-powerbi-mcp-server via deploy_via_api.py, secrets from 1Password.
# Same pattern as the other showcase MCP servers.

export PATH="/opt/homebrew/bin:${PATH}"

GITHUB_ITEM="op://Private/GitHub Personal Access Token IBB Tmp"
IMPALA_ITEM="op://Private/ibb-demo-token-test"
PBI_ITEM="op://Private/showcase-ibb-powerbi-sp"

export CAII_DOMAIN="dev-inference-service-01.dev-dmc.qcc475.b0.cloudera.site"
export GITHUB_USERNAME="cloudmista"

# Same warehouse and delegating service user as showcase-ibb-sql-mcp-server, used only to check which
# Impala objects a user may query. Adjust the host if the virtual warehouse is recreated.
export IMPALA_HOST="impala-proxy-dev-caaichris-ibb-showcase.dw-dev-dmc-base.qcc475.b0.cloudera.site"
export IMPALA_PORT=443
export IMPALA_HTTP_PATH=cliservice
export IMPALA_PROXY_USER="srv_ibb_demo_mcp_user"

export GITHUB_TOKEN
GITHUB_TOKEN="$(op read "${GITHUB_ITEM}/token")"
export IMPALA_PROXY_PASSWORD
IMPALA_PROXY_PASSWORD="$(op read "${IMPALA_ITEM}/impala-proxy-password")"

# Entra app registration used as service principal for the Power BI REST API, see README.
export PBI_TENANT_ID
PBI_TENANT_ID="$(op read "${PBI_ITEM}/tenant-id")"
export PBI_CLIENT_ID
PBI_CLIENT_ID="$(op read "${PBI_ITEM}/client-id")"
export PBI_CLIENT_SECRET
PBI_CLIENT_SECRET="$(op read "${PBI_ITEM}/client-secret")"

export CAII_TOKEN
CAII_TOKEN="$(cdp iam generate-workload-auth-token --workload-name DE | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${SCRIPT_DIR}/deploy_via_api.py"
