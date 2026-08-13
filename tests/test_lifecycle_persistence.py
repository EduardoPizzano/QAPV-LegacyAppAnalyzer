"""Incremento Lifecycle (2026-08-13): tests de la migracion aditiva de las 4
columnas nuevas en `apps` (build_date, last_activity_date,
last_activity_source, last_activity_confidence) y del round-trip
AnalysisResult -> save_analysis() -> get_app(). Corre siempre sobre una BD
temporal (via db.DB_PATH monkeypatcheado), nunca sobre qapv_analyzer.db real
-- mismo patron que test_save_analysis_dedup.py."""

import sqlite3

import pytest

from analyzer import confidence, db
from analyzer.activity import ActivityEvidence
from analyzer.techstack import TechStack

# DDL pre-incremento Lifecycle: exactamente las columnas de `apps` tal como
# existian antes de este incremento (companion_assemblies/review_status/
# review_notes ya existian de fases previas -- solo se omiten las 4 columnas
# nuevas de este incremento, para simular una BD real que nunca las tuvo).
PRE_LIFECYCLE_APPS_DDL = """
CREATE TABLE apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    dotnet_target TEXT,
    ui_framework TEXT,
    db_drivers TEXT,
    companion_assemblies TEXT,
    review_status TEXT DEFAULT 'borrador',
    review_notes TEXT
);
"""


@pytest.fixture
def pre_lifecycle_db(tmp_path, monkeypatch):
    """Crea una BD sintetica que representa el estado justo ANTES de este
    incremento, con una fila ya analizada, y apunta db.DB_PATH ahi."""
    db_path = tmp_path / "pre_lifecycle.db"
    conn = sqlite3.connect(db_path)
    conn.execute(PRE_LIFECYCLE_APPS_DDL)
    conn.execute(
        "INSERT INTO apps (name, source_path, analyzed_at, dotnet_target, ui_framework, db_drivers, "
        "companion_assemblies, review_status, review_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("AppVieja", r"\\server\AppVieja.exe", "2025-01-01T00:00:00", "net472", "WinForms",
         "System.Data.SqlClient", "", "logica_revisada", "Ya revisada antes de Lifecycle."),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", db_path)
    return db_path


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["Console/Service"], db_drivers=["System.Data.SqlClient"])


def _cols(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


NEW_COLUMNS = ("build_date", "last_activity_date", "last_activity_source", "last_activity_confidence")


class TestMigrationOnPreLifecycleDatabase:
    def test_init_db_does_not_raise_on_a_database_that_predates_this_increment(self, pre_lifecycle_db):
        db.init_db()  # no debe lanzar excepcion

    def test_new_columns_exist_after_migration(self, pre_lifecycle_db):
        db.init_db()
        with db.get_conn() as conn:
            cols = _cols(conn, "apps")
        for expected in NEW_COLUMNS:
            assert expected in cols

    def test_new_columns_are_null_on_the_preexisting_row_never_fabricated(self, pre_lifecycle_db):
        """NULL aqui significa 'analizada antes de que esta capacidad
        existiera' -- nunca se debe inventar un build_date ni una
        last_activity_source='no_evidence' retroactivamente para filas viejas
        (eso se reserva para filas nuevas donde SI se corrio la deteccion)."""
        db.init_db()
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM apps WHERE name = 'AppVieja'").fetchone()
        for col in NEW_COLUMNS:
            assert row[col] is None

    def test_preexisting_review_status_and_name_survive_migration(self, pre_lifecycle_db):
        db.init_db()
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM apps WHERE name = 'AppVieja'").fetchone()
        assert row["review_status"] == "logica_revisada"
        assert row["review_notes"] == "Ya revisada antes de Lifecycle."
        assert row["source_path"] == r"\\server\AppVieja.exe"

    def test_running_init_db_twice_on_the_migrated_database_is_safe(self, pre_lifecycle_db):
        db.init_db()
        db.init_db()  # no debe lanzar excepcion ni duplicar columnas
        with db.get_conn() as conn:
            cols = list(_cols(conn, "apps"))
        for expected in NEW_COLUMNS:
            assert cols.count(expected) == 1


class TestSaveAnalysisRoundTrip:
    def test_build_date_and_activity_survive_save_and_reload(self, temp_db):
        activity = ActivityEvidence(date="2026-05-20T08:37:48", source="file_log", confidence=60)

        app_id = db.save_analysis(
            "AppNueva", r"\\server\AppNueva.exe", _tech(), [], [], [], [],
            build_date="2025-07-10T16:39:44", activity=activity,
        )

        data = db.get_app(app_id)
        assert data["app"]["build_date"] == "2025-07-10T16:39:44"
        assert data["app"]["last_activity_date"] == "2026-05-20T08:37:48"
        assert data["app"]["last_activity_source"] == "file_log"
        assert data["app"]["last_activity_confidence"] == 60

    def test_omitting_activity_and_build_date_persists_as_no_evidence_not_as_an_error(self, temp_db):
        """Cubre llamadores viejos (tests existentes, o codigo futuro que no
        pase estos kwargs todavia) -- deben seguir funcionando exactamente
        como antes de este incremento, sin inventar evidencia."""
        app_id = db.save_analysis("AppSinLifecycle", r"\\server\AppSinLifecycle.exe", _tech(), [], [], [], [])

        data = db.get_app(app_id)
        assert data["app"]["build_date"] is None
        assert data["app"]["last_activity_date"] is None
        assert data["app"]["last_activity_source"] == "no_evidence"
        assert data["app"]["last_activity_confidence"] == confidence.UNKNOWN

    def test_reanalyzing_updates_lifecycle_fields_to_the_new_values(self, temp_db):
        exe = r"\\server\App.exe"
        db.save_analysis(
            "App", exe, _tech(), [], [], [], [],
            build_date="2025-01-01T00:00:00", activity=ActivityEvidence(date="2025-01-01T00:00:00", source="file_log", confidence=60),
        )
        app_id = db.save_analysis(
            "App", exe, _tech(), [], [], [], [],
            build_date="2026-08-01T00:00:00", activity=ActivityEvidence(date="2026-08-01T00:00:00", source="file_log", confidence=60),
        )

        data = db.get_app(app_id)
        assert data["app"]["build_date"] == "2026-08-01T00:00:00"
        assert data["app"]["last_activity_date"] == "2026-08-01T00:00:00"


class TestUpdateLifecycleBackfill:
    """update_lifecycle() -- backfill de Lifecycle para apps ya analizadas
    antes de que este incremento existiera, SIN re-decompilar ni re-correr
    save_analysis(). Debe tocar unicamente las 4 columnas de Lifecycle."""

    def test_updates_only_the_lifecycle_columns(self, temp_db):
        app_id = db.save_analysis("App", r"\\server\App.exe", _tech(), [], [], [], [])
        db.set_review(app_id, "logica_revisada", "Revision ya hecha antes del backfill.")

        db.update_lifecycle(
            app_id, "2025-07-10T16:39:44",
            ActivityEvidence(date="2026-05-20T08:37:48", source="file_log", confidence=60),
        )

        data = db.get_app(app_id)
        assert data["app"]["build_date"] == "2025-07-10T16:39:44"
        assert data["app"]["last_activity_date"] == "2026-05-20T08:37:48"
        assert data["app"]["last_activity_source"] == "file_log"
        assert data["app"]["last_activity_confidence"] == 60
        # Nada mas de la fila (identidad, revision manual) se altero.
        assert data["app"]["name"] == "App"
        assert data["app"]["source_path"] == r"\\server\App.exe"
        assert data["app"]["review_status"] == "logica_revisada"
        assert data["app"]["review_notes"] == "Revision ya hecha antes del backfill."

    def test_does_not_touch_sql_findings_or_settings(self, temp_db):
        from analyzer.evidence import Evidence
        from analyzer.extract import SettingEntry

        setting = SettingEntry(
            name="ConnString", default_value="Server=x;", is_connection_string=True,
            category="db", source_file="Settings.cs", evidence=Evidence(),
        )
        app_id = db.save_analysis("App", r"\\server\App.exe", _tech(), [setting], [], [], [])

        db.update_lifecycle(app_id, "2025-07-10T16:39:44", ActivityEvidence())

        data = db.get_app(app_id)
        assert len(data["settings"]) == 1
        assert data["settings"][0]["name"] == "ConnString"
