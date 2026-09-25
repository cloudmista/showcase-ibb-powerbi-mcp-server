import time
from collections.abc import Callable

import httpx

API_BASE = "https://api.powerbi.com/v1.0/myorg"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
TIMEOUT_SECONDS = 30
TOKEN_SAFETY_SECONDS = 60


class PowerBiError(RuntimeError):
    """Raised when Entra or the Power BI API rejects a call. The message never contains a token or secret."""

    def __init__(self, status: int, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class PowerBiClient:
    """
    Thin client for the few Power BI REST calls the server needs, authenticated as one service principal.

    Endpoints and body fields follow the Microsoft Learn reference pages for Datasets Refresh Dataset In Group,
    Get Refresh History In Group, Reports Clone Report In Group, Get Reports In Group and Delete Report In Group.
    The token request is the standard client credentials flow with the .default scope of the Power BI service.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS)
        self._clock = clock
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

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        response = self._http.request(
            method, f"{API_BASE}{path}", headers={"Authorization": f"Bearer {self._access_token()}"}, **kwargs
        )
        if response.status_code >= 400:
            raise PowerBiError(
                response.status_code,
                f"Power BI meldete HTTP {response.status_code}: {self._clean(response.text)}",
                response.headers.get("Retry-After"),
            )
        return response

    def refresh_dataset(self, group_id: str, dataset_id: str) -> str:
        """
        Trigger a standard refresh. With a service principal no mail notification is possible, so none is requested.

        :param group_id str: Workspace of the dataset
        :param dataset_id str: Dataset to refresh
        :return: The refresh request id from the x-ms-request-id header, empty if the header is missing
        """
        response = self._request(
            "POST", f"/groups/{group_id}/datasets/{dataset_id}/refreshes", json={"notifyOption": "NoNotification"}
        )
        return response.headers.get("x-ms-request-id", "")

    def refresh_history(self, group_id: str, dataset_id: str, top: int | None = None) -> list[dict[str, object]]:
        """
        Read the refresh history, newest first. Power BI returns the last 60 entries when top is omitted.

        :param group_id str: Workspace of the dataset
        :param dataset_id str: Dataset to inspect
        :param top int: Optional number of entries
        :return: Entries with status, refreshType, startTime, endTime, requestId and serviceExceptionJson
        """
        params = {"$top": top} if top else None
        return self._request("GET", f"/groups/{group_id}/datasets/{dataset_id}/refreshes", params=params).json()["value"]

    def clone_report(
        self, group_id: str, report_id: str, name: str, target_workspace_id: str, target_dataset_id: str
    ) -> dict[str, object]:
        """
        Clone a report into another workspace and bind the copy to a dataset.

        :param group_id str: Workspace of the template report
        :param report_id str: Template report
        :param name str: Name of the new report
        :param target_workspace_id str: Workspace the copy is created in
        :param target_dataset_id str: Dataset the copy is bound to
        :return: The new report with id, name, webUrl, embedUrl and datasetId
        """
        return self._request(
            "POST",
            f"/groups/{group_id}/reports/{report_id}/Clone",
            json={"name": name, "targetWorkspaceId": target_workspace_id, "targetModelId": target_dataset_id},
        ).json()

    def list_reports(self, group_id: str) -> list[dict[str, object]]:
        """
        List the reports of a workspace.

        :param group_id str: Workspace to list
        :return: Reports with id, name, webUrl, embedUrl and datasetId
        """
        return self._request("GET", f"/groups/{group_id}/reports").json()["value"]

    def delete_report(self, group_id: str, report_id: str) -> None:
        """
        Delete one report.

        :param group_id str: Workspace of the report
        :param report_id str: Report to delete
        """
        self._request("DELETE", f"/groups/{group_id}/reports/{report_id}")
