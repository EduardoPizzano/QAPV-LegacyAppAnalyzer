"""Incremento Data Lineage (2026-09): tests de las rutas Flask /lineage/*.
Mismo patron de BD temporal + test_client() que tests/test_app_map.py."""

import pytest

from analyzer import data_lineage, db
from analyzer.extract import SqlFinding
from analyzer.techstack import TechStack


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])


def _sql_finding(target, raw="", resolved=None, kind="CommandText", category="query"):
    return SqlFinding(
        file="Foo.cs", class_name="Foo", method="Bar", kind=kind, category=category,
        raw=raw, resolved=resolved, target=target,
    )


def _save(app_name, findings):
    db.save_analysis(app_name, f"\\\\server\\{app_name.replace('/', '_')}.exe", _tech(), [], findings, [], [])


def _app_id(app_name):
    return db.get_app_id_by_name(app_name)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_data_lineage_routes.db")
    db.init_db()


@pytest.fixture
def client(temp_db):
    import app as flask_app_module
    flask_app_module.app.testing = True
    return flask_app_module.app.test_client()


def _build_chain():
    _save("AppOracle", [
        _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
        _sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})"),
    ])
    _save("AppMiddle", [
        _sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}"),
        _sql_finding("TableY", resolved="Insert into TableY (IL) values ({a})"),
    ])
    _save("AppEnd", [_sql_finding("TableY", resolved="SELECT IL FROM TableY WHERE ID={id}")])


class TestOverviewRoute:
    def test_overview_before_any_build_shows_explicit_never_computed_state(self, client):
        resp = client.get("/lineage")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True).lower()
        assert "nunca se ha calculado" in body

    def test_overview_after_build_shows_real_classifications(self, client):
        _build_chain()
        client.post("/lineage/build")

        resp = client.get("/lineage")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "DIRECT_ORACLE" in body
        assert "AppOracle" in body
        assert "AppMiddle" in body

    def test_overview_classification_filter(self, client):
        """AppEnd sigue apareciendo en el sidebar de apps (eso no depende del
        filtro de linaje) -- lo que el filtro debe quitar es su FILA en la
        tabla de clasificacion, asi que se compara el conteo de ocurrencias
        en vez de una simple ausencia (encoding-safe, no depende de acentos)."""
        _build_chain()
        client.post("/lineage/build")

        unfiltered = client.get("/lineage").get_data(as_text=True)
        filtered = client.get("/lineage?classification=DIRECT_ORACLE").get_data(as_text=True)

        assert unfiltered.count("AppEnd") >= 2  # sidebar + fila de clasificacion
        assert filtered.count("AppEnd") == 1  # solo el sidebar, la fila fue filtrada
        assert "AppOracle" in filtered


class TestBuildRoute:
    def test_build_persists_a_run_and_redirects(self, client):
        _build_chain()
        resp = client.post("/lineage/build", follow_redirects=True)
        assert resp.status_code == 200
        assert "reconstruido" in resp.get_data(as_text=True).lower()
        assert db.get_latest_completed_lineage_run() is not None


class TestExplorerRoute:
    def test_no_selection_shows_the_search_form(self, client):
        resp = client.get("/lineage/explorer")
        assert resp.status_code == 200
        assert "Explorador de Linaje" in resp.get_data(as_text=True)

    def test_selecting_an_app_shows_downstream_chain(self, client):
        _build_chain()
        app_id = _app_id("AppOracle")

        resp = client.get(f"/lineage/explorer?app_id={app_id}")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "AppMiddle" in body
        assert "AppEnd" in body

    def test_selecting_by_object_name_round_trips(self, client):
        _build_chain()
        resp = client.get("/lineage/explorer?object=TableX")
        assert resp.status_code == 200
        assert "AppMiddle" in resp.get_data(as_text=True)


class TestApplicationAndObjectDetailRoutes:
    def test_application_detail_shows_classification(self, client):
        _build_chain()
        client.post("/lineage/build")
        app_id = _app_id("AppOracle")

        resp = client.get(f"/lineage/applications/{app_id}")
        assert resp.status_code == 200
        assert "DIRECT_ORACLE" in resp.get_data(as_text=True)

    def test_application_detail_for_unknown_app_redirects(self, client):
        resp = client.get("/lineage/applications/999999", follow_redirects=True)
        assert resp.status_code == 200
        assert "no existe" in resp.get_data(as_text=True)

    def test_object_detail_shows_fan_in_out(self, client):
        _build_chain()
        client.post("/lineage/build")

        resp = client.get("/lineage/objects/TableX")
        assert resp.status_code == 200
        assert "TableX" in resp.get_data(as_text=True)


class TestImpactAnalysisRoute:
    def test_impact_analysis_shows_direct_and_indirect_counts(self, client):
        _build_chain()
        client.post("/lineage/build")
        app_id = _app_id("AppOracle")

        resp = client.get(f"/lineage/impact?app_id={app_id}")
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "AppMiddle" in body
        assert "AppEnd" in body

    def test_impact_analysis_without_a_run_shows_guidance(self, client):
        resp = client.get("/lineage/impact")
        assert resp.status_code == 200
        assert "Construir" in resp.get_data(as_text=True) or "Refrescar" in resp.get_data(as_text=True)


class TestExistingRoutesUnaffected:
    def test_app_map_data_flow_portfolio_still_work(self, client):
        _build_chain()
        for path in ("/app_map", "/data_flow", "/portfolio", "/"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
