"""Home ejecutivo (2026-09-09): Oracle Connectivity / Data Providers / Data
Consumers / High Impact -- TODO derivado de db.get_ecosystem_summary(), que
a su vez SOLO lee la ultima corrida completada de Data Lineage
(lineage_app_classifications) via las funciones ya existentes
(list_lineage_classifications, list_critical_lineage_apps). Nunca recalcula
el grafo, nunca inventa un score de impacto nuevo.

Corre sobre una COPIA de qapv_analyzer.db -- mismo patron que
tests/test_functional_portfolio.py -- para poder verificar contra los
numeros reales de la corrida run_id=2 ya persistida."""

import shutil
from pathlib import Path

import pytest

from analyzer import db

REAL_DB_PATH = Path(__file__).parent.parent / "qapv_analyzer.db"

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


class TestEcosystemSummaryAgainstRealRun:
    def test_run_is_the_latest_completed_lineage_run(self, db_copy):
        summary = db.get_ecosystem_summary()
        latest = db.get_latest_completed_lineage_run()
        assert summary["run"]["id"] == latest["id"]

    def test_oracle_connected_matches_direct_oracle_classification_count(self, db_copy):
        summary = db.get_ecosystem_summary()
        run_id = summary["run"]["id"]
        direct_oracle = db.list_lineage_classifications(run_id, classification="DIRECT_ORACLE")
        assert summary["oracle_connected"] == len(direct_oracle)

    def test_data_providers_counts_apps_with_positive_fan_out(self, db_copy):
        summary = db.get_ecosystem_summary()
        run_id = summary["run"]["id"]
        all_classifications = db.list_lineage_classifications(run_id)
        expected = sum(1 for c in all_classifications if c["fan_out"] > 0)
        assert summary["data_providers"] == expected

    def test_data_consumers_counts_apps_with_positive_fan_in(self, db_copy):
        summary = db.get_ecosystem_summary()
        run_id = summary["run"]["id"]
        all_classifications = db.list_lineage_classifications(run_id)
        expected = sum(1 for c in all_classifications if c["fan_in"] > 0)
        assert summary["data_consumers"] == expected

    def test_high_impact_reuses_existing_is_critical_app_flag(self, db_copy):
        """No debe inventar un score nuevo -- debe ser EXACTAMENTE
        list_critical_lineage_apps(), el mecanismo ya existente."""
        summary = db.get_ecosystem_summary(top_n=5)
        run_id = summary["run"]["id"]
        expected = db.list_critical_lineage_apps(run_id, top_n=5)
        assert [a["app_id"] for a in summary["high_impact"]] == [a["app_id"] for a in expected]


class TestTopListsOrderingAndNoDuplicates:
    """Auditoria semantica 2026-09-09: Top Providers/Consumers ahora se
    ordenan por APPS UNICAS conectadas (unique_consumers/unique_providers),
    NUNCA por el hop-count crudo (fan_out/fan_in) -- ordenar por hops
    presentaria un numero de hops como si fuera relevancia de aplicaciones."""

    def test_top_providers_sorted_by_unique_consumers_descending(self, db_copy):
        summary = db.get_ecosystem_summary(top_n=10)
        counts = [c["unique_consumers"] for c in summary["top_providers"]]
        assert counts == sorted(counts, reverse=True)

    def test_top_consumers_sorted_by_unique_providers_descending(self, db_copy):
        summary = db.get_ecosystem_summary(top_n=10)
        counts = [c["unique_providers"] for c in summary["top_consumers"]]
        assert counts == sorted(counts, reverse=True)

    def test_top_providers_unique_consumers_uses_count_distinct_to_app_id(self, db_copy):
        """Verifica contra el SQL real (COUNT DISTINCT to_app_id), no contra
        el propio codigo que se esta probando."""
        summary = db.get_ecosystem_summary(top_n=10)
        run_id = summary["run"]["id"]
        with db.get_conn() as conn:
            for c in summary["top_providers"]:
                expected = conn.execute(
                    "SELECT COUNT(DISTINCT to_app_id) c FROM lineage_edges "
                    "WHERE lineage_run_id = ? AND from_app_id = ?",
                    (run_id, c["app_id"]),
                ).fetchone()["c"]
                assert c["unique_consumers"] == expected, c["app_name"]

    def test_top_consumers_unique_providers_uses_count_distinct_from_app_id(self, db_copy):
        summary = db.get_ecosystem_summary(top_n=10)
        run_id = summary["run"]["id"]
        with db.get_conn() as conn:
            for c in summary["top_consumers"]:
                expected = conn.execute(
                    "SELECT COUNT(DISTINCT from_app_id) c FROM lineage_edges "
                    "WHERE lineage_run_id = ? AND to_app_id = ?",
                    (run_id, c["app_id"]),
                ).fetchone()["c"]
                assert c["unique_providers"] == expected, c["app_name"]

    def test_hops_still_available_alongside_unique_counts(self, db_copy):
        """Cambio 5: los hops NO se eliminan, se conservan junto a las apps
        unicas -- ambas dimensiones deben seguir presentes en cada fila."""
        summary = db.get_ecosystem_summary(top_n=10)
        for c in summary["top_providers"]:
            assert "fan_out" in c and "unique_consumers" in c
        for c in summary["top_consumers"]:
            assert "fan_in" in c and "unique_providers" in c

    def test_datatransfer_v249_release_case_from_audit(self, db_copy):
        """Caso especifico validado en la auditoria: app_id=432 tiene
        fan_in=148 hops pero solo 38 apps origen unicas -- el Home NUNCA
        debe mostrar 148 bajo una etiqueta que implique aplicaciones."""
        summary = db.get_ecosystem_summary(top_n=137)
        row = next((c for c in summary["top_consumers"] if c["app_id"] == 432), None)
        assert row is not None, "DataTransfer v2.49/Release deberia aparecer en top_consumers"
        assert row["fan_in"] == 148
        assert row["unique_providers"] == 38

    def test_top_providers_no_duplicate_apps(self, db_copy):
        summary = db.get_ecosystem_summary(top_n=10)
        app_ids = [c["app_id"] for c in summary["top_providers"]]
        assert len(app_ids) == len(set(app_ids))

    def test_top_consumers_no_duplicate_apps(self, db_copy):
        summary = db.get_ecosystem_summary(top_n=10)
        app_ids = [c["app_id"] for c in summary["top_consumers"]]
        assert len(app_ids) == len(set(app_ids))


class TestNoLineageRunYet:
    def test_returns_explicit_empty_state_without_synthetic_zeros_hiding_absence(self, tmp_path, monkeypatch):
        """Sin corrida completada, run debe ser None -- el template usa esto
        para mostrar un estado explicito, nunca ceros disfrazados de dato real."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty_synthetic.db")
        db.init_db()
        summary = db.get_ecosystem_summary()
        assert summary["run"] is None
        assert summary["oracle_connected"] == 0
        assert summary["data_providers"] == 0
        assert summary["data_consumers"] == 0
        assert summary["top_providers"] == []
        assert summary["top_consumers"] == []
        assert summary["high_impact"] == []


class TestHomeRouteRendersEcosystemSection:
    def test_index_route_renders_connectivity_section_with_no_errors(self, db_copy):
        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        client = flask_app_module.app.test_client()
        response = client.get("/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Oracle Connected" in body
        assert "Data Providers" in body
        assert "Data Consumers" in body
        assert "High Impact Applications" in body

    def test_total_kpi_labeled_as_deployments_not_applications(self, db_copy):
        """Cambio 1: 137 debe presentarse como deployments, nunca como
        Application Identity -- el mecanismo de identidad no existe (ver
        ADR-0000/0001/0002, auditoria 2026-09-09)."""
        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        client = flask_app_module.app.test_client()
        client.get("/set_language/es")
        body = client.get("/").get_data(as_text=True)
        assert "Deployments Analizados" in body
        assert "Application Identity" in body  # nota metodologica en el tooltip

    def test_top_provider_consumer_tables_use_unique_app_labels_not_raw_hops(self, db_copy):
        """Cambio 3/4: las columnas deben decir 'apps unicas', nunca
        presentar el hop-count crudo bajo una etiqueta de aplicaciones."""
        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        client = flask_app_module.app.test_client()
        client.get("/set_language/en")
        body = client.get("/").get_data(as_text=True)
        assert "Unique Consuming Apps" in body
        assert "Unique Providing Apps" in body
        assert "Hops" in body
        # las viejas etiquetas ambiguas ("Consumers"/"Providers" como
        # encabezado de columna del hop-count) no deben quedar en su lugar
        assert "<th>Consumers</th>" not in body
        assert "<th>Providers</th>" not in body
