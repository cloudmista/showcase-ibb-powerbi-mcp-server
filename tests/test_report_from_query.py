from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from powerbi_mcp_server.powerbi import PowerBiError
from powerbi_mcp_server.query import QueryError, _cell, _column_type
from powerbi_mcp_server.service import MAX_REPORTS_PER_USER, PowerBiService, owner_tag
from powerbi_mcp_server.sql_guard import SqlValidationError, validate_select

W = "aaaaaaaa-0000-0000-0000-000000000001"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
CATALOG = {"agent_workspace_id": W, "datasets": {}, "templates": {}}
COLUMNS = ["bezirk", "jahr", "bewilligt"]
TYPES = ["string", "Int64", "Double"]
ROWS = [["Mitte", 2024, 10.5], ["Pankow", 2024, 7.0]]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.reports: list[dict] = []
        self.datasets: list[dict] = []

    def list_reports(self, group_id):
        return list(self.reports)

    def create_push_dataset(self, group_id, name, tables):
        self.calls.append(("dataset", group_id, name, tables))
        return {"id": "ds-1", "name": name}

    def add_rows(self, group_id, dataset_id, table, rows):
        self.calls.append(("rows", dataset_id, table, len(rows)))

    def delete_dataset(self, group_id, dataset_id):
        self.calls.append(("delete_dataset", dataset_id))

    def list_datasets(self, group_id):
        return list(self.datasets)

    def delete_report(self, group_id, report_id):
        self.calls.append(("delete_report", report_id))


class FakeFabric:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.error: PowerBiError | None = None

    def create_report(self, workspace_id, name, parts):
        if self.error:
            raise self.error
        self.created.append((workspace_id, name, parts))
        return {"id": "rep-1"}


class FakeRunner:
    def __init__(self, result=(COLUMNS, TYPES, ROWS), error: QueryError | None = None) -> None:
        self.result, self.error, self.calls = result, error, []

    def run(self, user, sql):
        self.calls.append((user, sql))
        if self.error:
            raise self.error
        return self.result


def make(runner=None):
    client, fabric = FakeClient(), FakeFabric()
    runner = runner or FakeRunner()
    return PowerBiService(CATALOG, client, None, now=lambda: NOW, fabric=fabric, query_runner=runner), client, fabric, runner


def test_the_query_runs_as_the_user_and_only_its_rows_are_pushed() -> None:
    service, client, fabric, runner = make()
    result = service.create_report_from_query("alice", "SELECT 1", "Bewilligungen", "bezirk", "bewilligt", "jahr")
    assert runner.calls == [("alice", "SELECT 1")]
    assert result == {"report_id": "rep-1", "name": "Bewilligungen", "web_url": f"https://app.powerbi.com/groups/{W}/reports/rep-1", "rows": 2}
    _, _, name, tables = client.calls[0]
    assert name == "Bewilligungen" + owner_tag("alice", "2026-09-25")
    assert tables[0]["columns"] == [{"name": "bezirk", "dataType": "string"}, {"name": "jahr", "dataType": "Int64"}, {"name": "bewilligt", "dataType": "Double"}]
    assert tables[0]["measures"] == [{"name": "Summe bewilligt", "expression": "SUM(Daten[bewilligt])"}]
    assert ("rows", "ds-1", "Daten", 2) in client.calls
    assert fabric.created[0][1] == name


def test_rows_are_pushed_in_batches() -> None:
    rows = [["Mitte", 2024, float(i)] for i in range(2500)]
    service, client, *_ = make(FakeRunner((COLUMNS, TYPES, rows)))
    service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt")
    assert [c[3] for c in client.calls if c[0] == "rows"] == [1000, 1000, 500]


def test_average_uses_average_in_dax() -> None:
    service, client, *_ = make()
    service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt", aggregation="average")
    assert client.calls[0][3][0]["measures"] == [{"name": "Durchschnitt bewilligt", "expression": "AVERAGE(Daten[bewilligt])"}]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"dimension_column": "nope"}, "dimension_column"),
        ({"measure_column": "nope"}, "measure_column"),
        ({"date_column": "nope"}, "date_column"),
        ({"measure_column": "bezirk"}, "nicht numerisch"),
        ({"aggregation": "median"}, "sum oder average"),
        ({"title": "  "}, "titel"),
    ],
)
def test_invalid_requests_are_refused_before_anything_is_pushed(kwargs, fragment) -> None:
    service, client, fabric, _ = make()
    args = {"sql": "SELECT 1", "title": "T", "dimension_column": "bezirk", "measure_column": "bewilligt"} | kwargs
    result = service.create_report_from_query("alice", **args)
    assert result["error_type"] == "validation"
    assert fragment.lower() in result["error"].lower()
    assert client.calls == [] and fabric.created == []


def test_a_refused_query_is_reported_and_nothing_is_created() -> None:
    service, client, _, _ = make(FakeRunner(error=QueryError("Abfrage fehlgeschlagen: AuthorizationException")))
    result = service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt")
    assert result["error_type"] == "query"
    assert client.calls == []


def test_a_failed_report_creation_removes_the_pushed_dataset() -> None:
    service, client, fabric, _ = make()
    fabric.error = PowerBiError(500, "kaputt")
    result = service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt")
    assert result["error_type"] == "powerbi"
    assert ("delete_dataset", "ds-1") in client.calls


def test_the_per_user_report_limit_applies() -> None:
    service, client, _, runner = make()
    client.reports = [{"id": f"r{i}", "name": f"X{owner_tag('alice', '2026-09-25')}"} for i in range(MAX_REPORTS_PER_USER)]
    result = service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt")
    assert result["error_type"] == "limit"
    assert runner.calls == []


def test_an_untrusted_user_name_is_refused() -> None:
    service, _, _, runner = make()
    assert service.create_report_from_query("", "SELECT 1", "T", "bezirk", "bewilligt")["error_type"] == "validation"
    assert runner.calls == []


def test_deleting_a_report_also_deletes_its_dataset() -> None:
    service, client, *_ = make()
    name = "T" + owner_tag("alice", "2026-09-25")
    client.reports = [{"id": "rep-1", "name": name}]
    client.datasets = [{"id": "ds-1", "name": name}, {"id": "ds-2", "name": "Fremd"}]
    assert service.delete_report("alice", "rep-1") == {"deleted": True}
    assert ("delete_report", "rep-1") in client.calls
    assert ("delete_dataset", "ds-1") in client.calls
    assert ("delete_dataset", "ds-2") not in client.calls


def test_catalog_tools_report_placeholders_but_the_query_tool_does_not_need_the_catalog() -> None:
    catalog = CATALOG | {"datasets": {"a": {"dataset_id": "00000000-0000-0000-0000-000000000000"}}}
    service = PowerBiService(catalog, FakeClient(), None, now=lambda: NOW, fabric=FakeFabric(), query_runner=FakeRunner())
    assert "Platzhalter" in service.list_datasets("alice")["error"]
    assert "report_id" in service.create_report_from_query("alice", "SELECT 1", "T", "bezirk", "bewilligt")


def test_select_guard_accepts_selects_and_rejects_everything_else() -> None:
    validate_select("WITH x AS (SELECT 1 AS a) SELECT a FROM x")
    for bad in ("", "DROP TABLE t", "SELECT 1; SELECT 2", "INSERT INTO t VALUES (1)", "SHOW TABLES", "UPDATE t SET a = 1"):
        with pytest.raises(SqlValidationError):
            validate_select(bad)


def test_column_types_and_cells_follow_the_driver_values() -> None:
    assert _column_type([None, 3]) == "Int64"
    assert _column_type([Decimal("1.5")]) == "Double"
    assert _column_type([True]) == "string"
    assert _column_type([date(2026, 1, 2)]) == "DateTime"
    assert _column_type([None]) == "string"
    assert _cell(Decimal("1.5"), "Double") == 1.5
    assert _cell(date(2026, 1, 2), "DateTime") == "2026-01-02"
    assert _cell(None, "Int64") is None
