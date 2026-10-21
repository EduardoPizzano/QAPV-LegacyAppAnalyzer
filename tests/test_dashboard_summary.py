"""Portada/Dashboard principal (2026-09-09): las 4 tarjetas (Apps en Piso,
Resto de Apps, Deployments Analizados, Apps Pendientes) leen db.get_portfolio_summary(),
que reutiliza EXACTAMENTE la misma particion que group_apps_for_sidebar()
(via _floor_classification_by_app_id()) -- nunca una segunda heuristica de
clasificacion. Corre sobre una COPIA de qapv_analyzer.db con el seed real
importado, mismo patron que tests/test_functional_portfolio.py."""

import json
import shutil
from pathlib import Path

import pytest

from analyzer import db

REAL_DB_PATH = Path(__file__).parent.parent / "qapv_analyzer.db"
REFERENCE_DATA_DIR = Path(__file__).parent.parent / "analyzer" / "reference_data"

pytestmark = pytest.mark.skipif(
    not REAL_DB_PATH.is_file(),
    reason="qapv_analyzer.db no existe en esta maquina (no versionado, ver .gitignore)",
)


@pytest.fixture
def db_copy(tmp_path, monkeypatch):
    copy_path = tmp_path / "qapv_analyzer_test_copy.db"
    shutil.copy(REAL_DB_PATH, copy_path)
    monkeypatch.setattr(db, "DB_PATH", copy_path)
    db.init_db()
    return copy_path


@pytest.fixture
def loaded_summary(db_copy):
    inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
    mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
    db.import_functional_portfolio(inventory_rows, mapping_rows)
    return db.get_portfolio_summary()


class TestDashboardSummaryCounts:
    """Todos los valores deben venir de la BD/logica existente -- nunca
    hardcodeados. Los numeros 60/54/137/16/23 son el resultado ESPERADO de
    los datos reales actuales, no una constante que el codigo deba producir
    por si sola."""

    def test_floor_count_is_60(self, loaded_summary):
        assert loaded_summary["floor_count"] == 60

    def test_rest_count_is_54(self, loaded_summary):
        assert loaded_summary["rest_count"] == 54

    def test_total_count_is_137(self, loaded_summary):
        assert loaded_summary["total"] == 137

    def test_pending_count_is_16(self, loaded_summary):
        assert loaded_summary["pending"] == 16

    def test_obsolete_count_is_23(self, loaded_summary):
        assert loaded_summary["obsolete"] == 23

    def test_floor_plus_rest_plus_obsolete_equals_total(self, loaded_summary):
        s = loaded_summary
        assert s["floor_count"] + s["rest_count"] + s["obsolete"] == s["total"] == 137


class TestFloorPlusRestNeverEqualsTotalRegression:
    """Guarda contra la regresion explicita que el usuario pidio detectar:
    mientras existan aplicaciones obsoletas, floor_count + rest_count NUNCA
    debe ser igual a total -- si alguien "simplifica" el calculo del
    dashboard a total = floor + rest, este test debe fallar."""

    def test_floor_plus_rest_is_less_than_total_while_obsolete_exist(self, loaded_summary):
        s = loaded_summary
        assert s["obsolete"] > 0, "esta prueba requiere que existan apps obsoletas en la BD real"
        assert s["floor_count"] + s["rest_count"] != s["total"]
        assert s["floor_count"] + s["rest_count"] == s["total"] - s["obsolete"]


class TestDashboardSameSourceAsSidebar:
    """get_portfolio_summary() y group_apps_for_sidebar() deben coincidir
    exactamente -- son la MISMA clasificacion, nunca dos calculos distintos
    que puedan divergir."""

    def test_floor_count_matches_sidebar_piso_section(self, db_copy):
        inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
        mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
        db.import_functional_portfolio(inventory_rows, mapping_rows)

        summary = db.get_portfolio_summary()
        sidebar = db.group_apps_for_sidebar()
        piso_section = next(it for it in sidebar if it["kind"] == "floor_section" and it["section"] == "piso")
        resto_section = next(it for it in sidebar if it["kind"] == "floor_section" and it["section"] == "resto")
        obsolete_section = next(it for it in sidebar if it["kind"] == "obsolete_section")

        assert summary["floor_count"] == piso_section["count"]
        assert summary["rest_count"] == resto_section["count"]
        assert summary["obsolete"] == len(obsolete_section["apps"])


class TestDashboardRouteRendersCorrectCounts:
    def _client(self, db_copy):
        inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
        mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
        db.import_functional_portfolio(inventory_rows, mapping_rows)

        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        return flask_app_module.app.test_client()

    def test_spanish_dashboard_shows_all_four_labels_and_counts(self, db_copy):
        client = self._client(db_copy)
        client.get("/set_language/es")
        body = client.get("/").get_data(as_text=True)
        # "Apps Analizadas" -> "Deployments Analizados" (auditoria semantica
        # 2026-09-09): apps.id es un Deployment, no una Application Identity
        # (ver ADR-0000/0001/0002/0004) -- el label debe reflejar eso.
        for label in ("Apps en Piso", "Resto de Apps", "Deployments Analizados", "Apps Pendientes"):
            assert label in body
        assert "aplicaciones obsoletas" in body
        assert ">60<" in body
        assert ">54<" in body
        assert ">137<" in body
        assert ">16<" in body

    def test_english_dashboard_shows_all_four_labels(self, db_copy):
        client = self._client(db_copy)
        client.get("/set_language/en")
        body = client.get("/").get_data(as_text=True)
        for label in ("Operational Floor Apps", "Rest of Portfolio", "Deployments Analyzed", "Pending Apps"):
            assert label in body
        assert "obsolete applications" in body
        # los literales en espanol nunca deben quedar sin traducir en ingles
        for spanish_label in ("Apps en Piso", "Resto de Apps", "Deployments Analizados", "Apps Pendientes"):
            assert spanish_label not in body

    def test_dashboard_cards_link_to_existing_routes_only(self, db_copy):
        """Apps en Piso / Resto de Apps enlazan a anchors del propio sidebar
        (misma pagina); Deployments Analizados enlaza a /portfolio (ruta ya
        existente). Apps Pendientes queda informativa -- no existe ruta de
        filtro por pendientes, y la instruccion explicita fue no inventar una."""
        client = self._client(db_copy)
        body = client.get("/").get_data(as_text=True)
        assert "#sidebar-piso" in body
        assert "#sidebar-resto" in body
        assert '/portfolio' in body
        assert "#sidebar-obsoletas" in body
