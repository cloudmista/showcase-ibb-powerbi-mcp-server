"""
Deploy showcase-ibb-powerbi-mcp-server to Cloudera AI Inference directly via its REST API.

Schema source: swagger.json served next to the public API reference page
https://docs.cloudera.com/machine-learning/cloud/rest-api-reference-ai-inference-service/,
fetched and inspected directly on 2026-09-24, not guessed - same schema showcase-ibb-chat's
deploy_via_api.py already verified against. runtime_image has no documented catalog/default (no
list-runtimes endpoint exists in this API at all, confirmed by listing every path in the swagger
file); its value here was captured live from the Cloudera console's own successful
deployApplication request for showcase-ibb-chat via DevTools Network tab on 2026-09-24 - the same
generic Python runtime works here too, this app is also a plain Python process.

Reads secrets from environment variables only, never prints them, never logs the request body
verbatim.
"""

import json
import os
import sys
import urllib.error
import urllib.request

REQUIRED_ENV = ("CAII_DOMAIN", "CAII_TOKEN", "GITHUB_USERNAME", "GITHUB_TOKEN")

# App environment variables, forwarded to the deployed Application if set locally. See
# README.md's "Umgebungsvariablen" table for what each one does.
APP_ENV_PASSTHROUGH = (
    "IMPALA_HOST",
    "IMPALA_PORT",
    "IMPALA_HTTP_PATH",
    "IMPALA_CA_CERT",
    "IMPALA_PROXY_USER",
    "IMPALA_PROXY_PASSWORD",
    "IMPALA_VERIFY_TLS",
    "PBI_TENANT_ID",
    "PBI_CLIENT_ID",
    "PBI_CLIENT_SECRET",
    "POWERBI_CATALOG_PATH",
    "MCP_TRANSPORT",
)
SECRET_ENV_NAMES = ("IMPALA_PROXY_PASSWORD", "PBI_CLIENT_SECRET")

RUNTIME_IMAGE = "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.12-standard:2025.09.1-b5"


def app_environment_variables() -> list[dict[str, str]]:
    """
    Read the optional app environment variables from the local shell environment.

    :return: List of {"name": ..., "value": ...} entries for every APP_ENV_PASSTHROUGH var that is set
    """
    return [{"name": name, "value": os.environ[name]} for name in APP_ENV_PASSTHROUGH if os.environ.get(name)]


def app_exists(domain: str, token: str, name: str, namespace: str) -> bool:
    """
    Check whether an Application with this name already exists, so deployApplication's is_update
    flag can be set correctly instead of guessed/hardcoded.

    Found live on 2026-09-24: a deleted app's pod terminates faster than its Application record is
    purged server-side, so a plain "create" (is_update absent/false) briefly collides with a
    still-existing record (HTTP 409), while a stale "update" (is_update true) fails once the
    record is genuinely gone (the authorizer's own metadata lookup fails). Checking live avoids
    guessing either way.

    :param domain str: Inference service domain
    :param token str: CAII bearer token
    :param name str: Application name to look for
    :param namespace str: Namespace the application would be deployed into
    :return: True if an application with this name currently exists in this namespace
    """
    url = f"https://{domain}/api/v1alpha1/listApplications"
    request = urllib.request.Request(
        url,
        data=json.dumps({"namespace": namespace}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode())
    return any(app.get("name") == name for app in body.get("applications", []))


def build_request(domain: str, github_username: str, github_token: str, is_update: bool) -> dict[str, object]:
    """
    Build the deployApplication request body for showcase-ibb-powerbi-mcp-server.

    :param domain str: Inference service domain, for example dev-inference-service-01.dev-dmc.qcc475.b0.cloudera.site
    :param github_username str: GitHub account that owns the personal access token
    :param github_token str: GitHub PAT with repo access to fluxraum/showcase-ibb-powerbi-mcp-server
    :param is_update bool: Whether an application with this name already exists (see app_exists)
    :return: Request body dict
    """
    directory_source: dict[str, object] = {
        "url": "https://github.com/fluxraum/showcase-ibb-powerbi-mcp-server.git",
        "entry_point": "start.sh",
        "runtime_image": RUNTIME_IMAGE,
        "credentials": {
            "github_credentials": {
                "username": github_username,
                "access_token": github_token,
            }
        },
    }
    env_vars = app_environment_variables()
    if env_vars:
        directory_source["environment_variables"] = env_vars
    return {
        "name": "showcase-ibb-powerbi-mcp-server",
        "subdomain": "showcase-ibb-powerbi-mcp-server",
        "namespace": "serving-apps",
        "description": "Power BI MCP server, template based reports and scoped refreshes, MCP over Streamable HTTP.",
        "source": {"directory_source": directory_source},
        "resources": {"req_cpu": "1", "req_memory": "2Gi", "num_gpus": "0"},
        # autoscaling_config was the missing piece for is_web_app:false, found 2026-09-24 by
        # capturing the Cloudera console's own UI request via DevTools - without it, istiod
        # rejects the pod's Envoy listener config outright and it never becomes Ready. With it,
        # the pod starts cleanly and internal calls get a plain 401 instead of a 302 redirect to
        # Knox's browser SSO login (confirmed live against a throwaway test app).
        "autoscaling": {
            "min_replicas": "1",
            "max_replicas": "1",
            "autoscaling_config": {"metric": "rps", "target": "200"},
        },
        # False: this server is only ever called by showcase-ibb-chat's backend, never a browser.
        "is_web_app": False,
        "ml_user_permission": "access",
        "is_update": is_update,
    }


def redact(body: dict[str, object]) -> dict[str, object]:
    """
    Copy the request body with the GitHub token and secret env values masked, for safe printing.

    :param body dict: The full request body
    :return: A deep-enough copy with access_token and secret env values replaced
    """
    copy = json.loads(json.dumps(body))
    directory_source = copy.get("source", {}).get("directory_source", {})
    github = directory_source.get("credentials", {}).get("github_credentials")
    if github and "access_token" in github:
        github["access_token"] = "***"
    for entry in directory_source.get("environment_variables", []):
        if entry.get("name") in SECRET_ENV_NAMES:
            entry["value"] = "***"
    return copy


def main() -> int:
    """
    Send the deploy request and print the response, with secrets masked.

    :return: Process exit code
    """
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    domain = os.environ["CAII_DOMAIN"]
    token = os.environ["CAII_TOKEN"]
    is_update = app_exists(domain, token, "showcase-ibb-powerbi-mcp-server", "serving-apps")
    print(f"App already exists: {is_update}")
    body = build_request(domain, os.environ["GITHUB_USERNAME"], os.environ["GITHUB_TOKEN"], is_update)

    print("Request body (secrets masked):")
    print(json.dumps(redact(body), indent=2))

    url = f"https://{domain}/api/v1alpha1/deployApplication"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"\nStatus: {response.status}")
            print(response.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"\nHTTP {exc.code}", file=sys.stderr)
        print(exc.read().decode(), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"\nConnection failed: {exc.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
