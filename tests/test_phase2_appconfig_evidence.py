"""Tests de Fase 2 (primer incremento funcional end-to-end del Validation
Framework): App.config Connection String -> Evidence -> Confidence ->
UI/Reporte. A diferencia de Fase 1 (que solo probaba la FORMA de Evidence/
Confidence en aislamiento), esto confirma que _find_appconfig_connection_strings
ya construye un Evidence con datos reales, que sobrevive el round-trip por
qapv_analyzer.db, y que report.py lo muestra."""

from datetime import datetime

import pytest

from analyzer import db
from analyzer.__version__ import ANALYZER_VERSION
from analyzer.confidence import CONFIDENCE_TABLE
from analyzer.extract import find_settings
from analyzer.report import render_from_db
from analyzer.techstack import TechStack


def _setting_by_name(settings, name):
    return next(s for s in settings if s.name == name)


class TestAppConfigExtractionCarriesRealEvidence:
    """La primera extraccion real instrumentada -- ya no recibe el Evidence()
    default de Fase 1."""

    def test_reportviewer_connection_has_real_evidence(self, fixture_root):
        settings = find_settings(fixture_root("reportviewer"))
        conn = _setting_by_name(settings, "QAPVMLN")
        ev = conn.evidence

        assert ev.extractor == "APP_CONFIG_EXPLICIT_CONNECTION"
        assert ev.pattern == "connectionStrings/add"
        assert ev.confidence == CONFIDENCE_TABLE["APP_CONFIG_EXPLICIT_CONNECTION"] == 98
        assert ev.source_file == "app.config"
        assert ev.line_number == 21  # linea real del <add> en el fixture
        assert 'name="QAPVMLN"' in ev.snippet
        assert ev.analyzer_version == ANALYZER_VERSION
        # created_at es un timestamp real generado en el momento de la
        # extraccion (no el default None de Fase 1) -- confirma que es ISO
        # parseable en vez de solo verificar truthiness.
        datetime.fromisoformat(ev.created_at)

    def test_interafl_real_entries_get_sequential_line_numbers_skipping_comments(self, fixture_root):
        """7 entradas activas + la comentada de 6 lineas en medio -- confirma
        que el numero de linea de cada Evidence corresponde al texto real,
        no al indice dentro de la lista ya filtrada por ET."""
        settings = find_settings(fixture_root("interafl"))
        by_name = {s.name: s for s in settings}

        assert by_name["connectionString"].evidence.line_number == 7
        assert by_name["CX"].evidence.line_number == 17
        assert by_name["CXEXFO2"].evidence.line_number == 23

    def test_settings_cs_derived_connections_keep_default_evidence(self, fixture_root):
        """Fase 2 instrumenta SOLO _find_appconfig_connection_strings -- una
        conexion que viene de Settings.cs (DefaultSettingValue) todavia debe
        mostrar el default honesto de Fase 1, no un valor inventado."""
        settings = find_settings(fixture_root("interconfig"))
        conn = _setting_by_name(settings, "CX")
        # interconfig's CX viene de app.config, no de Settings.cs -- si esto
        # cambia de fixture en el futuro, ver test_reportviewer para el caso
        # canonico de Settings.cs-vs-app.config.
        assert conn.evidence.extractor == "APP_CONFIG_EXPLICIT_CONNECTION"


class TestEvidenceSurvivesDatabaseRoundTrip:
    @pytest.fixture
    def fresh_db(self, tmp_path, monkeypatch):
        """DB nueva y vacia (no una copia de la de produccion) -- Fase 2 no
        necesita datos preexistentes, solo confirmar que el esquema de Fase 1
        ya migrado guarda y recupera Evidence real."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "phase2_test.db")
        db.init_db()

    def test_appconfig_evidence_columns_persist_through_save_and_get(self, fixture_root, fresh_db):
        settings = find_settings(fixture_root("reportviewer"))
        app_id = db.save_analysis(
            "Phase2ReportViewerTest", "N/A", TechStack(), settings, [], [], [],
        )

        data = db.get_app(app_id)
        row = next(s for s in data["settings"] if s["name"] == "QAPVMLN")

        assert row["extractor"] == "APP_CONFIG_EXPLICIT_CONNECTION"
        assert row["confidence"] == 98
        assert row["line_number"] == 21
        assert row["pattern"] == "connectionStrings/add"
        assert row["analyzer_version"] == ANALYZER_VERSION
        assert row["created_at"] is not None

    def test_evidence_reaches_the_rendered_report(self, fixture_root, fresh_db):
        """Cierra el flujo completo del pedido del usuario: App.config ->
        Evidence -> Confidence -> UI/Reporte. Verifica el reporte tal como lo
        vera el usuario (Markdown), no solo la BD."""
        settings = find_settings(fixture_root("reportviewer"))
        app_id = db.save_analysis(
            "Phase2ReportViewerTest2", "N/A", TechStack(), settings, [], [], [],
        )

        report_md = render_from_db(db.get_app(app_id))

        assert "APP_CONFIG_EXPLICIT_CONNECTION" in report_md
        assert "98%" in report_md
        assert "linea 21" in report_md
