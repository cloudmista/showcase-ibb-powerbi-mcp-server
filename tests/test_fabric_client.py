import json
from urllib.parse import parse_qs

import httpx
import pytest

from powerbi_mcp_server.fabric import FabricClient
from powerbi_mcp_server.powerbi import PowerBiError

W = "w-1"
PARTS = [{"path": "definition.pbir", "payload": "e30=", "payloadType": "InlineBase64"}]


def make(handler) -> tuple[FabricClient, list[httpx.Request], list[float]]:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "login.microsoftonline.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return handler(request)

    client = FabricClient("tenant-1", "client-1", "s3cret", transport=httpx.MockTransport(wrapped), sleep=sleeps.append)
    return client, seen, sleeps


def test_token_request_uses_the_fabric_scope() -> None:
    client, seen, _ = make(lambda r: httpx.Response(200, json={"value": [{"id": W, "displayName": "IBB"}]}))
    assert client.list_workspaces() == [{"id": W, "displayName": "IBB"}]
    assert parse_qs(seen[0].content.decode())["scope"] == ["https://api.fabric.microsoft.com/.default"]
    assert str(seen[1].url) == "https://api.fabric.microsoft.com/v1/workspaces"
    assert seen[1].headers["Authorization"] == "Bearer tok"


def test_list_semantic_models_calls_the_workspace_endpoint() -> None:
    client, seen, _ = make(lambda r: httpx.Response(200, json={"value": [{"id": "m-1", "displayName": "Modell"}]}))
    assert client.list_semantic_models(W) == [{"id": "m-1", "displayName": "Modell"}]
    assert str(seen[1].url) == f"https://api.fabric.microsoft.com/v1/workspaces/{W}/semanticModels"


def test_create_report_returns_the_item_on_201() -> None:
    client, seen, sleeps = make(lambda r: httpx.Response(201, json={"id": "r-1", "displayName": "Bericht"}))
    assert client.create_report(W, "Bericht", PARTS)["id"] == "r-1"
    body = json.loads(seen[1].content)
    assert body == {"displayName": "Bericht", "definition": {"parts": PARTS}}
    assert str(seen[1].url) == f"https://api.fabric.microsoft.com/v1/workspaces/{W}/reports"
    assert sleeps == []


def test_create_report_polls_the_operation_on_202_and_reads_the_result() -> None:
    op = "https://api.fabric.microsoft.com/v1/operations/op-1"
    states = iter([{"status": "Running"}, {"status": "Succeeded"}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": op, "Retry-After": "7"})
        if str(request.url) == f"{op}/result":
            return httpx.Response(200, json={"id": "r-2", "displayName": "Bericht"})
        return httpx.Response(200, json=next(states))

    client, _, sleeps = make(handler)
    assert client.create_report(W, "Bericht", PARTS)["id"] == "r-2"
    assert sleeps == [7.0, 7.0]


def test_a_failed_operation_raises_with_the_fabric_error() -> None:
    op = "https://api.fabric.microsoft.com/v1/operations/op-1"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": op})
        return httpx.Response(200, json={"status": "Failed", "error": {"errorCode": "CorruptedPayload", "message": "bad part"}})

    client, _, _ = make(handler)
    with pytest.raises(PowerBiError, match="CorruptedPayload"):
        client.create_report(W, "Bericht", PARTS)


def test_http_errors_carry_status_and_never_the_secret() -> None:
    client, _, _ = make(lambda r: httpx.Response(403, text="denied s3cret"))
    with pytest.raises(PowerBiError) as caught:
        client.list_workspaces()
    assert caught.value.status == 403
    assert "s3cret" not in str(caught.value)
    assert "***" in str(caught.value)


def test_get_report_definition_reads_parts_directly_or_through_the_operation() -> None:
    parts = [{"path": "definition.pbir", "payload": "e30=", "payloadType": "InlineBase64"}]
    client, seen, _ = make(lambda r: httpx.Response(200, json={"definition": {"parts": parts}}))
    assert client.get_report_definition(W, "r-1") == parts
    assert str(seen[1].url) == f"https://api.fabric.microsoft.com/v1/workspaces/{W}/reports/r-1/getDefinition"

    op = "https://api.fabric.microsoft.com/v1/operations/op-9"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, headers={"Location": op})
        if str(request.url) == f"{op}/result":
            return httpx.Response(200, json={"definition": {"parts": parts}})
        return httpx.Response(200, json={"status": "Succeeded"})

    client, _, _ = make(handler)
    assert client.get_report_definition(W, "r-1") == parts
