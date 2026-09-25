from datetime import datetime, timedelta, timezone

import pytest

from powerbi_mcp_server.powerbi import PowerBiError
from powerbi_mcp_server.service import MAX_REPORTS_PER_USER, PowerBiService, owner_tag, parse_owner

A, B, C, D = ("aaaaaaaa-0000-0000-0000-00000000000%d" % i for i in range(1, 5))
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

CATALOG = {
    "agent_workspace_id": A,
    "datasets": {
        "foerderung": {"workspace_id": B, "dataset_id": C, "name": "Förderung", "description": "F", "requires": ["finance_foerderung.v_foerderung_official"], "max_refreshes_per_day": 2, "tables": [{"name": "T"}], "measures": [{"name": "M"}]},
        "portfolio": {"workspace_id": B, "dataset_id": D, "name": "Portfolio", "description": "P", "requires": ["finance_portfolio.v_portfolio_official"]},
    },
    "templates": {
        "uebersicht": {"workspace_id": B, "report_id": "t-1", "name": "Übersicht", "description": "U", "dataset_keys": ["foerderung", "portfolio"]},
        "nur-foerderung": {"workspace_id": B, "report_id": "t-2", "name": "NF", "description": "N", "dataset_keys": ["foerderung"]},
    },
}


class FakeChecker:
    def __init__(self, rights: dict[str, set[str]]) -> None:
        self.rights = rights

    def allowed_objects(self, user: str, names: set[str]) -> set[str]:
        return names & self.rights.get(user, set())


class FakeClient:
    def __init__(self) -> None:
        self.reports: list[dict] = []
        self.history: list[dict] = []
        self.calls: list[tuple] = []
        self.error: PowerBiError | None = None

    def _maybe_fail(self) -> None:
        if self.error:
            raise self.error

    def list_reports(self, group_id):
        self._maybe_fail()
        return list(self.reports)

    def clone_report(self, group_id, report_id, name, target_workspace_id, target_dataset_id):
        self._maybe_fail()
        self.calls.append(("clone", group_id, report_id, name, target_workspace_id, target_dataset_id))
        return {"id": "new-1", "webUrl": "https://app/new-1"}

    def delete_report(self, group_id, report_id):
        self.calls.append(("delete", group_id, report_id))

    def refresh_history(self, group_id, dataset_id, top=None):
        return self.history[:top] if top else list(self.history)

    def refresh_dataset(self, group_id, dataset_id):
        self.calls.append(("refresh", group_id, dataset_id))
        return "req-1"


ALL = {"finance_foerderung.v_foerderung_official", "finance_portfolio.v_portfolio_official"}


@pytest.fixture()
def env():
    client = FakeClient()
    checker = FakeChecker({"alice": set(ALL), "bob": {"finance_foerderung.v_foerderung_official"}})
    return PowerBiService(CATALOG, client, checker, now=lambda: NOW), client


def report(name: str, rid: str = "r1", dataset: str = C) -> dict:
    return {"id": rid, "name": name, "webUrl": f"https://app/{rid}", "datasetId": dataset}


def test_owner_tag_round_trips() -> None:
    assert parse_owner("Mein Bericht" + owner_tag("a.b@x", "2026-09-25")) == ("a.b@x", "2026-09-25")
    assert parse_owner("ohne Tag") is None


def test_users_only_see_datasets_they_may_query(env) -> None:
    service, _ = env
    assert [d["key"] for d in service.list_datasets("alice")["datasets"]] == ["foerderung", "portfolio"]
    assert [d["key"] for d in service.list_datasets("bob")["datasets"]] == ["foerderung"]
    assert service.list_datasets("carol")["datasets"] == []


def test_an_unknown_and_a_forbidden_dataset_look_identical(env) -> None:
    service, _ = env
    assert service.semantic_model("bob", "portfolio") == service.semantic_model("bob", "gibt-es-nicht")
    assert service.semantic_model("bob", "portfolio")["error_type"] == "denied"


def test_semantic_model_comes_from_the_catalog(env) -> None:
    service, _ = env
    result = service.semantic_model("alice", "foerderung")
    assert (result["tables"], result["measures"]) == ([{"name": "T"}], [{"name": "M"}])


def test_templates_are_limited_to_usable_datasets(env) -> None:
    service, _ = env
    bob = service.list_templates("bob")["templates"]
    assert {t["key"]: t["dataset_keys"] for t in bob} == {"uebersicht": ["foerderung"], "nur-foerderung": ["foerderung"]}
    assert service.list_templates("bob", "portfolio")["error_type"] == "denied"


def test_create_report_clones_the_template_into_the_agent_workspace(env) -> None:
    service, client = env
    result = service.create_report("alice", "uebersicht", "foerderung", "Q3 Übersicht")
    assert client.calls == [("clone", B, "t-1", "Q3 Übersicht [agent:alice:2026-09-25]", A, C)]
    assert result == {"report_id": "new-1", "name": "Q3 Übersicht [agent:alice:2026-09-25]", "web_url": "https://app/new-1"}


def test_create_report_refuses_forbidden_datasets_and_incompatible_templates(env) -> None:
    service, client = env
    assert service.create_report("bob", "uebersicht", "portfolio", "x")["error_type"] == "denied"
    assert service.create_report("alice", "nur-foerderung", "portfolio", "x")["error_type"] == "denied"
    assert service.create_report("alice", "gibt-es-nicht", "foerderung", "x")["error_type"] == "denied"
    assert client.calls == []


def test_report_names_are_sanitized_and_cannot_forge_a_tag(env) -> None:
    service, client = env
    service.create_report("alice", "uebersicht", "foerderung", "Böse [agent:bob:2020-01-01]\n" + "x" * 100)
    name = client.calls[0][3]
    assert parse_owner(name) == ("alice", "2026-09-25")
    assert name.count("[") == 1
    assert service.create_report("alice", "uebersicht", "foerderung", " [] ")["error_type"] == "validation"


def test_report_count_per_user_is_capped(env) -> None:
    service, client = env
    client.reports = [report("R" + owner_tag("alice", "2026-09-01"), f"r{i}") for i in range(MAX_REPORTS_PER_USER)]
    client.reports.append(report("Fremd" + owner_tag("bob", "2026-09-01"), "rb"))
    assert service.create_report("alice", "uebersicht", "foerderung", "neu")["error_type"] == "limit"
    assert service.create_report("bob", "uebersicht", "foerderung", "neu")["report_id"] == "new-1"


def test_listing_shows_only_own_reports_without_the_tag(env) -> None:
    service, client = env
    client.reports = [report("Mein" + owner_tag("alice", "2026-09-20"), "r1"), report("Ihr" + owner_tag("bob", "2026-09-21"), "r2"), report("Handgemacht", "r3")]
    assert service.list_reports("alice")["reports"] == [{"report_id": "r1", "name": "Mein", "created": "2026-09-20", "web_url": "https://app/r1"}]


def test_status_shows_the_latest_refresh_of_the_bound_dataset(env) -> None:
    service, client = env
    client.reports = [report("Mein" + owner_tag("alice", "2026-09-20"), "r1", dataset=C)]
    client.history = [{"status": "Failed", "startTime": "2026-09-25T10:00:00Z", "endTime": "2026-09-25T10:05:00Z", "serviceExceptionJson": "{\"errorCode\":\"x\"}"}]
    result = service.report_status("alice", "r1")
    assert result["last_refresh"] == {"status": "Failed", "started": "2026-09-25T10:00:00Z", "finished": "2026-09-25T10:05:00Z", "error": "{\"errorCode\":\"x\"}"}
    assert result["web_url"] == "https://app/r1"
    assert service.report_status("bob", "r1")["error_type"] == "denied"


def test_delete_only_reaches_own_reports(env) -> None:
    service, client = env
    client.reports = [report("Mein" + owner_tag("alice", "2026-09-20"), "r1"), report("Ihr" + owner_tag("bob", "2026-09-20"), "r2")]
    assert service.delete_report("alice", "r2")["error_type"] == "denied"
    assert client.calls == []
    assert service.delete_report("alice", "r1") == {"deleted": True}
    assert client.calls == [("delete", A, "r1")]


def hist(minutes_ago: float, status: str = "Completed", finished: bool = True) -> dict:
    start = NOW - timedelta(minutes=minutes_ago)
    entry = {"status": status, "startTime": start.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    if finished:
        entry["endTime"] = entry["startTime"]
    return entry


def test_refresh_starts_when_all_limits_allow_it(env) -> None:
    service, client = env
    client.history = [hist(60), hist(60 * 30)]
    assert service.refresh_dataset("alice", "foerderung") == {"refresh_id": "req-1", "status": "Accepted"}
    assert client.calls == [("refresh", B, C)]


@pytest.mark.parametrize(
    "history",
    [
        [hist(5, "Unknown", finished=False)],
        [hist(5)],
        [hist(60), hist(120)],
    ],
    ids=["already running", "within ten minutes", "daily cap of two reached"],
)
def test_refresh_is_refused_by_the_limits(env, history) -> None:
    service, client = env
    client.history = history
    assert service.refresh_dataset("alice", "foerderung")["error_type"] == "limit"
    assert client.calls == []


def test_old_history_entries_do_not_count_toward_the_daily_cap(env) -> None:
    service, client = env
    client.history = [hist(60 * 25), hist(60 * 26), hist(60 * 27)]
    assert service.refresh_dataset("alice", "foerderung")["status"] == "Accepted"


def test_refresh_of_a_forbidden_dataset_never_reaches_power_bi(env) -> None:
    service, client = env
    assert service.refresh_dataset("bob", "portfolio")["error_type"] == "denied"
    assert client.calls == []


def test_power_bi_errors_become_error_results_with_retry_after(env) -> None:
    service, client = env
    client.error = PowerBiError(429, "zu viele Anfragen", retry_after="30")
    result = service.list_reports("alice")
    assert result == {"error": "zu viele Anfragen", "error_type": "powerbi", "retry_after": "30"}


@pytest.mark.parametrize("user", ["", "a b", "x]", "a/b", "a" * 101])
def test_untrusted_user_names_are_rejected_before_any_lookup(env, user) -> None:
    service, client = env
    assert service.list_datasets(user)["error_type"] == "validation"
    assert service.create_report(user, "uebersicht", "foerderung", "x")["error_type"] == "validation"
    assert client.calls == []


def test_without_any_ranger_right_nothing_is_available(env) -> None:
    service, client = env
    assert service.list_datasets("nobody")["datasets"] == []
    assert service.create_report("nobody", "uebersicht", "foerderung", "x")["error_type"] == "denied"
    assert service.refresh_dataset("nobody", "foerderung")["error_type"] == "denied"
