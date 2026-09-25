import json
from urllib.parse import parse_qs

import httpx
import pytest

from powerbi_mcp_server.powerbi import PowerBiClient, PowerBiError

G, D, R = "g-1", "d-1", "r-1"


def make(handler, clock=lambda: 0.0) -> tuple[PowerBiClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "login.microsoftonline.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return handler(request)

    return PowerBiClient("tenant-1", "client-1", "s3cret", transport=httpx.MockTransport(wrapped), clock=clock), seen


def test_token_request_uses_client_credentials_and_the_power_bi_scope() -> None:
    client, seen = make(lambda r: httpx.Response(200, json={"value": []}))
    client.list_reports(G)
    token_request = seen[0]
    assert str(token_request.url) == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["client_credentials"]
    assert form["scope"] == ["https://analysis.windows.net/powerbi/api/.default"]
    assert seen[1].headers["Authorization"] == "Bearer tok"


def test_the_token_is_reused_until_shortly_before_it_expires() -> None:
    now = [0.0]
    client, seen = make(lambda r: httpx.Response(200, json={"value": []}), clock=lambda: now[0])
    client.list_reports(G)
    client.list_reports(G)
    assert sum("login.microsoft" in str(r.url) for r in seen) == 1
    now[0] = 3600.0
    client.list_reports(G)
    assert sum("login.microsoft" in str(r.url) for r in seen) == 2


def test_refresh_posts_to_the_dataset_and_returns_the_request_id() -> None:
    client, seen = make(lambda r: httpx.Response(202, headers={"x-ms-request-id": "req-9"}))
    assert client.refresh_dataset(G, D) == "req-9"
    request = seen[-1]
    assert (request.method, str(request.url)) == ("POST", f"https://api.powerbi.com/v1.0/myorg/groups/{G}/datasets/{D}/refreshes")
    assert json.loads(request.content) == {"notifyOption": "NoNotification"}


def test_refresh_history_passes_top_only_when_given() -> None:
    client, seen = make(lambda r: httpx.Response(200, json={"value": [{"status": "Completed"}]}))
    assert client.refresh_history(G, D, top=1) == [{"status": "Completed"}]
    assert seen[-1].url.params["$top"] == "1"
    client.refresh_history(G, D)
    assert "$top" not in seen[-1].url.params


def test_clone_sends_name_target_workspace_and_target_dataset() -> None:
    client, seen = make(lambda r: httpx.Response(200, json={"id": "new", "webUrl": "https://x"}))
    assert client.clone_report(G, R, "Name", "agent-ws", "ds-2")["id"] == "new"
    request = seen[-1]
    assert (request.method, str(request.url)) == ("POST", f"https://api.powerbi.com/v1.0/myorg/groups/{G}/reports/{R}/Clone")
    assert json.loads(request.content) == {"name": "Name", "targetWorkspaceId": "agent-ws", "targetModelId": "ds-2"}


def test_delete_uses_the_delete_method() -> None:
    client, seen = make(lambda r: httpx.Response(200))
    client.delete_report(G, R)
    assert (seen[-1].method, str(seen[-1].url)) == ("DELETE", f"https://api.powerbi.com/v1.0/myorg/groups/{G}/reports/{R}")


def test_api_errors_carry_status_and_retry_after_but_never_the_secret() -> None:
    client, _ = make(lambda r: httpx.Response(429, text="zu viele s3cret Anfragen", headers={"Retry-After": "30"}))
    with pytest.raises(PowerBiError) as caught:
        client.refresh_dataset(G, D)
    assert caught.value.status == 429
    assert caught.value.retry_after == "30"
    assert "s3cret" not in str(caught.value)


def test_a_failed_token_request_is_reported_without_the_secret() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid_client s3cret")

    client = PowerBiClient("t", "c", "s3cret", transport=httpx.MockTransport(transport))
    with pytest.raises(PowerBiError) as caught:
        client.list_reports(G)
    assert "s3cret" not in str(caught.value)
    assert caught.value.status == 401
