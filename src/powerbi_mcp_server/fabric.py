import time
from collections.abc import Callable

import httpx

from powerbi_mcp_server.powerbi import TIMEOUT_SECONDS, TOKEN_SAFETY_SECONDS, TOKEN_URL, PowerBiError

API_BASE = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
DEFAULT_POLL_SECONDS = 5
MAX_POLLS = 60


class FabricClient:
    """
    Thin client for the Fabric REST calls the server needs, authenticated as one service principal.

    Endpoints follow the Microsoft Learn reference pages for Workspaces List Workspaces, Items List Semantic Models,
    Items Create Report and the long running operation pattern (202, Location header, operation state, result).
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires = 0.0

    def _clean(self, text: str) -> str:
        return text.replace(self._client_secret, "***")[:400]

    def _access_token(self) -> str:
        if self._token and self._clock() < self._token_expires:
            return self._token
        response = self._http.post(
            TOKEN_URL.format(tenant=self._tenant_id),
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": SCOPE,
            },
        )
        if response.status_code != 200:
            raise PowerBiError(response.status_code, f"Token-Anfrage fehlgeschlagen: {self._clean(response.text)}")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires = self._clock() + int(body.get("expires_in", 3600)) - TOKEN_SAFETY_SECONDS
        return self._token

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        response = self._http.request(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, **kwargs)
        if response.status_code >= 400:
            raise PowerBiError(
                response.status_code,
                f"Fabric meldete HTTP {response.status_code}: {self._clean(response.text)}",
                response.headers.get("Retry-After"),
            )
        return response

    def list_workspaces(self) -> list[dict[str, object]]:
        """
        List the workspaces the service principal can see.

        :return: Workspace objects with id and displayName
        :raises PowerBiError: If Fabric rejects the call
        """
        return self._request("GET", f"{API_BASE}/workspaces").json().get("value", [])

    def list_semantic_models(self, workspace_id: str) -> list[dict[str, object]]:
        """
        List the semantic models of one workspace.

        :param workspace_id str: Workspace id
        :return: Semantic model objects with id and displayName
        :raises PowerBiError: If Fabric rejects the call
        """
        return self._request("GET", f"{API_BASE}/workspaces/{workspace_id}/semanticModels").json().get("value", [])

    def create_report(self, workspace_id: str, display_name: str, parts: list[dict[str, str]]) -> dict[str, object]:
        """
        Create a report from definition parts and wait for the long running operation if Fabric starts one.

        :param workspace_id str: Target workspace id
        :param display_name str: Name of the new report
        :param parts list: Definition parts from pbir.build_report_parts
        :return: The created report item with id and displayName
        :raises PowerBiError: If Fabric rejects the call, the operation fails or does not finish in time
        """
        response = self._request(
            "POST",
            f"{API_BASE}/workspaces/{workspace_id}/reports",
            json={"displayName": display_name, "definition": {"parts": parts}},
        )
        if response.status_code == 201:
            return response.json()
        if response.status_code != 202:
            raise PowerBiError(response.status_code, f"Unerwartete Antwort von Fabric: HTTP {response.status_code}")
        return self._wait_for_result(response.headers["Location"], response.headers.get("Retry-After"))

    def _wait_for_result(self, operation_url: str, retry_after: str | None) -> dict[str, object]:
        delay = float(retry_after) if retry_after and retry_after.isdigit() else DEFAULT_POLL_SECONDS
        for _ in range(MAX_POLLS):
            self._sleep(delay)
            state = self._request("GET", operation_url).json()
            status = state.get("status")
            if status == "Succeeded":
                return self._request("GET", f"{operation_url}/result").json()
            if status == "Failed":
                error = state.get("error") or {}
                raise PowerBiError(500, f"Fabric konnte den Bericht nicht erstellen: {self._clean(str(error.get('errorCode', '')))} {self._clean(str(error.get('message', '')))}")
        raise PowerBiError(504, "Fabric hat die Berichtserstellung nicht rechtzeitig abgeschlossen")
