import logging
import os
import socket

from fastmcp import FastMCP

from .access import AccessChecker
from .catalog import CatalogError, load_catalog, workspace_configured
from .fabric import FabricClient
from .powerbi import PowerBiClient
from .query import ImpalaQueryRunner
from .service import PowerBiService

logging.basicConfig(level=logging.INFO)

mcp = FastMCP(name="IBB Power BI MCP Server")
_service: PowerBiService | None = None


def get_service() -> PowerBiService:
    """
    Build the service on first use, so a missing setting produces a clear tool error instead of a crashed server.

    :return: The shared PowerBiService
    :raises RuntimeError: If settings are missing or the catalog still holds placeholder GUIDs
    """
    global _service
    if _service is None:
        missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
        if missing:
            raise RuntimeError(f"Server nicht konfiguriert, fehlende Einstellungen: {', '.join(missing)}")
        try:
            catalog = load_catalog()
        except CatalogError as exc:
            raise RuntimeError(f"Katalog fehlerhaft: {exc}") from exc
        if not workspace_configured(catalog):
            raise RuntimeError("Der Agent-Workspace im Katalog ist noch ein Platzhalter, siehe README")
        credentials = (os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"])
        _service = PowerBiService(
            catalog, PowerBiClient(*credentials), AccessChecker(), fabric=FabricClient(*credentials), query_runner=ImpalaQueryRunner()
        )
    return _service


def _run(call) -> dict[str, object]:
    try:
        return call(get_service())
    except RuntimeError as exc:
        return {"error": str(exc), "error_type": "configuration"}


@mcp.tool()
def list_powerbi_datasets(acting_as_user: str) -> dict[str, object]:
    """
    List the Power BI datasets acting_as_user may use, with description and compatible report templates.

    acting_as_user must be the trusted, SSO-verified username, never a value invented by the LLM.
    """
    return _run(lambda s: s.list_datasets(acting_as_user))


@mcp.tool()
def get_powerbi_semantic_model(dataset_key: str, acting_as_user: str) -> dict[str, object]:
    """
    Describe the tables and measures of one dataset, as documented in the catalog.
    """
    return _run(lambda s: s.semantic_model(acting_as_user, dataset_key))


@mcp.tool()
def list_report_templates(acting_as_user: str, dataset_key: str | None = None) -> dict[str, object]:
    """
    List the curated report templates usable with the datasets acting_as_user may use.
    """
    return _run(lambda s: s.list_templates(acting_as_user, dataset_key))


@mcp.tool()
def create_powerbi_report(template_key: str, dataset_key: str, report_name: str, acting_as_user: str) -> dict[str, object]:
    """
    Create a report from a curated template, bound to a dataset, in the agent workspace. Returns the link.
    The layout is fixed by the template, free-form visuals are not possible.
    """
    return _run(lambda s: s.create_report(acting_as_user, template_key, dataset_key, report_name))


@mcp.tool()
def create_report_from_query(
    sql: str,
    title: str,
    dimension_column: str,
    measure_column: str,
    acting_as_user: str,
    date_column: str | None = None,
    aggregation: str = "sum",
) -> dict[str, object]:
    """
    Run one read-only SELECT as acting_as_user and turn exactly its result into a Power BI report with a card, a bar
    chart of measure_column by dimension_column and an optional slicer on date_column. Aggregate in the SQL so the
    result stays small. Returns the report link. Only the user's own query result is sent to Power BI.
    """
    return _run(lambda s: s.create_report_from_query(acting_as_user, sql, title, dimension_column, measure_column, date_column, aggregation))


@mcp.tool()
def list_my_reports(acting_as_user: str) -> dict[str, object]:
    """
    List the reports acting_as_user generated earlier.
    """
    return _run(lambda s: s.list_reports(acting_as_user))


@mcp.tool()
def get_report_status(report_id: str, acting_as_user: str) -> dict[str, object]:
    """
    Show link and latest dataset refresh of one generated report of acting_as_user.
    """
    return _run(lambda s: s.report_status(acting_as_user, report_id))


@mcp.tool()
def delete_generated_report(report_id: str, acting_as_user: str) -> dict[str, object]:
    """
    Delete one generated report of acting_as_user. Other reports cannot be deleted.
    """
    return _run(lambda s: s.delete_report(acting_as_user, report_id))


@mcp.tool()
def refresh_powerbi_dataset(dataset_key: str, acting_as_user: str) -> dict[str, object]:
    """
    Trigger a refresh of a dataset. Limited to one running refresh, ten minutes between refreshes and a daily cap.
    """
    return _run(lambda s: s.refresh_dataset(acting_as_user, dataset_key))


def main() -> None:
    """
    Run the MCP server with the same transport conventions as the other showcase servers.
    """
    socket.setdefaulttimeout(60)
    transport = os.environ.get("MCP_TRANSPORT", "http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host="0.0.0.0", port=int(os.environ.get("APP_PORT", "8080")))


if __name__ == "__main__":
    main()
