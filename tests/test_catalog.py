import copy
import json

import pytest

from powerbi_mcp_server import catalog as cat

G = "11111111-1111-1111-1111-111111111111"


def valid() -> dict:
    return {
        "agent_workspace_id": G,
        "datasets": {"foerderung": {"workspace_id": G, "dataset_id": G, "name": "F", "description": "d", "requires": ["db.view"]}},
        "templates": {"uebersicht": {"workspace_id": G, "report_id": G, "name": "U", "description": "d", "dataset_keys": ["foerderung"]}},
    }


def test_a_valid_catalog_passes() -> None:
    cat.validate_catalog(valid())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(agent_workspace_id="kein-guid"),
        lambda c: c["datasets"]["foerderung"].update(requires=[]),
        lambda c: c["datasets"]["foerderung"].update(requires=["nur_name"]),
        lambda c: c["datasets"]["foerderung"].update(requires=["db.view; DROP"]),
        lambda c: c["datasets"]["foerderung"].update(max_refreshes_per_day=9),
        lambda c: c["datasets"]["foerderung"].update(name=""),
        lambda c: c["datasets"].update({"Falsch_Key": copy.deepcopy(c["datasets"]["foerderung"])}),
        lambda c: c["templates"]["uebersicht"].update(dataset_keys=["gibt-es-nicht"]),
        lambda c: c["templates"]["uebersicht"].update(dataset_keys=[]),
        lambda c: c.update(datasets={}),
    ],
)
def test_a_malformed_catalog_is_rejected(mutate) -> None:
    catalog = valid()
    mutate(catalog)
    with pytest.raises(cat.CatalogError):
        cat.validate_catalog(catalog)


def test_placeholder_guids_mark_the_catalog_as_not_configured() -> None:
    catalog = valid()
    assert cat.is_configured(catalog)
    catalog["templates"]["uebersicht"]["report_id"] = cat.PLACEHOLDER_GUID
    assert not cat.is_configured(catalog)


def test_load_catalog_reports_unreadable_files(tmp_path) -> None:
    with pytest.raises(cat.CatalogError):
        cat.load_catalog(tmp_path / "fehlt.json")
    broken = tmp_path / "kaputt.json"
    broken.write_text("{")
    with pytest.raises(cat.CatalogError):
        cat.load_catalog(broken)


def test_the_committed_catalog_is_structurally_valid_but_still_unconfigured() -> None:
    catalog = json.loads(cat.DEFAULT_PATH.read_text())
    cat.validate_catalog(catalog)
    assert not cat.is_configured(catalog)
