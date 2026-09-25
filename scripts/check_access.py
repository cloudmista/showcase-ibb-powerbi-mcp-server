"""
Read-only sign-in test for the Power BI service principal. Needs PBI_TENANT_ID, PBI_CLIENT_ID and PBI_CLIENT_SECRET in the
environment. Requests one token per API (Fabric and Power BI), lists the workspaces the principal can see and prints
names and ids. Never prints a token or the secret.

Usage:
PBI_TENANT_ID=$(op read ...) PBI_CLIENT_ID=$(op read ...) PBI_CLIENT_SECRET=$(op read ...) python3 scripts/check_access.py
"""

import os
import sys

import httpx

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
APIS = (
    ("Fabric", "https://api.fabric.microsoft.com/.default", "https://api.fabric.microsoft.com/v1/workspaces", "value", "displayName", "id"),
    ("Power BI", "https://analysis.windows.net/powerbi/api/.default", "https://api.powerbi.com/v1.0/myorg/groups", "value", "name", "id"),
)


def get_token(tenant: str, client_id: str, client_secret: str, scope: str) -> str:
    """
    Request an app-only token with the client credentials flow.

    :param tenant str: Directory (tenant) ID
    :param client_id str: Application (client) ID
    :param client_secret str: Client secret value
    :param scope str: The .default scope of the target API
    :return: The access token
    :raises RuntimeError: If Entra rejects the request, with the error code and description only
    """
    response = httpx.post(
        TOKEN_URL.format(tenant=tenant),
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret, "scope": scope},
        timeout=30,
    )
    body = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"Entra {response.status_code}: {body.get('error')} {str(body.get('error_description', ''))[:300]}")
    return body["access_token"]


def main() -> int:
    missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    failures = 0
    for label, scope, url, list_key, name_key, id_key in APIS:
        print(f"== {label}")
        try:
            token = get_token(os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"], scope)
        except RuntimeError as exc:
            print(f"  Token FAILED: {exc}")
            failures += 1
            continue
        print("  Token OK")
        response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if response.status_code != 200:
            print(f"  Workspace list FAILED: HTTP {response.status_code} {response.text[:300]}")
            failures += 1
            continue
        items = response.json().get(list_key, [])
        print(f"  Workspace list OK, {len(items)} visible")
        for item in items:
            print(f"    {item.get(name_key)}  {item.get(id_key)}")
            if label == "Fabric":
                models = httpx.get(f"{url}/{item.get(id_key)}/semanticModels", headers={"Authorization": f"Bearer {token}"}, timeout=30)
                for model in models.json().get("value", []) if models.status_code == 200 else []:
                    print(f"      Semantic model: {model.get('displayName')}  {model.get('id')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
