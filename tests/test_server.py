import asyncio

import pytest

from powerbi_mcp_server import server

TOOLS = {
    "list_powerbi_datasets": ["acting_as_user"],
    "get_powerbi_semantic_model": ["dataset_key", "acting_as_user"],
    "list_report_templates": ["acting_as_user", "dataset_key"],
    "create_powerbi_report": ["template_key", "dataset_key", "report_name", "acting_as_user"],
    "list_my_reports": ["acting_as_user"],
    "get_report_status": ["report_id", "acting_as_user"],
    "delete_generated_report": ["report_id", "acting_as_user"],
    "refresh_powerbi_dataset": ["dataset_key", "acting_as_user"],
}


@pytest.mark.parametrize("name", list(TOOLS))
def test_every_tool_takes_the_user_as_a_parameter_the_chat_backend_sets(name: str) -> None:
    tool = asyncio.run(server.mcp.get_tool(name))
    assert list(tool.parameters["properties"]) == TOOLS[name]
    assert "acting_as_user" in tool.parameters["required"]


def test_missing_settings_give_a_clear_tool_error_instead_of_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "_service", None)
    result = server.list_powerbi_datasets.fn("alice") if hasattr(server.list_powerbi_datasets, "fn") else server._run(lambda s: s.list_datasets("alice"))
    assert result["error_type"] == "configuration"
    assert "PBI_TENANT_ID" in result["error"]


def test_placeholder_catalog_blocks_all_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(server, "_service", None)
    result = server._run(lambda s: s.list_datasets("alice"))
    assert result["error_type"] == "configuration"
    assert "Platzhalter" in result["error"]
