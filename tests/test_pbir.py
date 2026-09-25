import base64
import json

from powerbi_mcp_server.pbir import NAME_PATTERN, Field, build_report_parts

MEASURE = Field("Foerderung", "Bewilligt")
DIMENSION = Field("Foerderung", "Bezirk")
DATE = Field("Foerderung", "Jahr")


def decoded(parts: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    return {p["path"]: json.loads(base64.b64decode(p["payload"])) for p in parts}


def test_parts_contain_the_required_pbir_files_and_are_inline_base64() -> None:
    parts = build_report_parts("model-1", "Bewilligungen", MEASURE, DIMENSION, DATE)
    assert {p["payloadType"] for p in parts} == {"InlineBase64"}
    files = decoded(parts)
    for required in ("definition.pbir", "definition/version.json", "definition/report.json", "definition/pages/pages.json"):
        assert required in files
    assert sum(path.endswith("/page.json") for path in files) == 1
    assert sum(path.endswith("/visual.json") for path in files) == 3


def test_report_binds_to_the_semantic_model_by_id_only() -> None:
    files = decoded(build_report_parts("model-1", "Titel", MEASURE, DIMENSION))
    assert files["definition.pbir"]["datasetReference"] == {"byConnection": {"connectionString": "semanticmodelid=model-1"}}
    assert files["definition.pbir"]["version"] == "4.0"


def test_page_and_visual_names_follow_the_naming_convention_and_match_their_paths() -> None:
    files = decoded(build_report_parts("model-1", "Titel", MEASURE, DIMENSION, DATE))
    pages = files["definition/pages/pages.json"]
    page_name = pages["pageOrder"][0]
    assert pages["activePageName"] == page_name
    assert NAME_PATTERN.match(page_name)
    assert files[f"definition/pages/{page_name}/page.json"]["name"] == page_name
    for path, body in files.items():
        if path.endswith("/visual.json"):
            assert NAME_PATTERN.match(body["name"])
            assert path == f"definition/pages/{page_name}/visuals/{body['name']}/visual.json"


def test_visuals_reference_the_requested_fields() -> None:
    files = decoded(build_report_parts("model-1", "Titel", MEASURE, DIMENSION, DATE))
    visuals = {b["visual"]["visualType"]: b["visual"]["query"]["queryState"] for p, b in files.items() if p.endswith("/visual.json")}
    assert set(visuals) == {"card", "barChart", "slicer"}
    assert visuals["card"]["Values"]["projections"][0]["queryRef"] == "Foerderung.Bewilligt"
    assert visuals["card"]["Values"]["projections"][0]["field"]["Measure"]["Property"] == "Bewilligt"
    assert visuals["barChart"]["Category"]["projections"][0]["field"]["Column"]["Property"] == "Bezirk"
    assert visuals["barChart"]["Y"]["projections"][0]["field"]["Measure"]["Property"] == "Bewilligt"
    assert visuals["slicer"]["Values"]["projections"][0]["queryRef"] == "Foerderung.Jahr"


def test_the_date_slicer_is_optional() -> None:
    files = decoded(build_report_parts("model-1", "Titel", MEASURE, DIMENSION))
    assert sum(path.endswith("/visual.json") for path in files) == 2


def test_titles_are_quoted_literals_and_escape_single_quotes() -> None:
    files = decoded(build_report_parts("model-1", "Titel", MEASURE, Field("T", "Land's End")))
    bar = next(b for p, b in files.items() if p.endswith("/visual.json") and b["visual"]["visualType"] == "barChart")
    value = bar["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
    assert value == "'Titel'"
    assert Field("T", "Land's End").query_ref == "T.Land's End"


def test_output_is_deterministic() -> None:
    assert build_report_parts("model-1", "Titel", MEASURE, DIMENSION) == build_report_parts("model-1", "Titel", MEASURE, DIMENSION)
