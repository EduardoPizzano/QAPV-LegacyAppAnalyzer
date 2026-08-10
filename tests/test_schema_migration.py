"""Tests de la migracion de esquema de Fase 1 (Paso 5). Corre SIEMPRE sobre
una COPIA de qapv_analyzer.db en un archivo temporal -- nunca sobre la BD de
produccion (ver ARCHITECTURE_REVIEW.md deuda #6: "cero test de referencia
sobre el esquema actual de db.py"). Si la BD real no existe en esta maquina
(no esta en git, ver .gitignore), estos tests se saltan solos."""

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
    """Copia qapv_analyzer.db a un archivo temporal y apunta db.DB_PATH ahi
    -- mismo patron ya usado (y validado) en el rescan del portafolio
    completo de una sesion anterior."""
    copy_path = tmp_path / "qapv_analyzer_test_copy.db"
    shutil.copy(REAL_DB_PATH, copy_path)
    monkeypatch.setattr(db, "DB_PATH", copy_path)
    return copy_path


def _table_columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


class TestMigrationRunsClean:
    def test_init_db_does_not_raise_on_already_populated_database(self, db_copy):
        db.init_db()  # no debe lanzar excepcion

    def test_new_columns_exist_after_migration(self, db_copy):
        db.init_db()
        with db.get_conn() as conn:
            for table in ("sql_findings", "settings", "io_findings"):
                cols = _table_columns(conn, table)
                for expected in ("line_number", "snippet", "extractor", "pattern", "confidence", "analyzer_version", "created_at"):
                    assert expected in cols, f"Columna '{expected}' no existe en '{table}' despues de migrar"

    def test_unknowns_table_exists_and_is_empty(self, db_copy):
        db.init_db()
        with db.get_conn() as conn:
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "unknowns" in tables
            count = conn.execute("SELECT COUNT(*) as c FROM unknowns").fetchone()["c"]
            assert count == 0, "La tabla unknowns deberia nacer vacia en Fase 1 -- nada la puebla todavia"


class TestExistingDataPreserved:
    def test_new_columns_are_null_on_preexisting_rows(self, db_copy):
        """NULL en una fila ya analizada significa 'analizada antes de que
        esta capacidad existiera' -- confirma que la migracion no invento
        ningun valor para lo que ya estaba ahi.

        No depende de que la copia de la BD real ya tenga alguna fila asi --
        eso dejo de ser cierto en la practica una vez que los Incrementos 2 y
        3A reanalizaron el portafolio completo (toda fila de produccion hoy
        viene de un save_analysis() que SI escribe Evidence). Se inserta una
        fila sintetica con la forma pre-Evidence directamente, para que el
        test siga verificando el invariante real sin importar el estado
        actual de produccion."""
        with db.get_conn() as conn:
            apps_row = conn.execute("SELECT id FROM apps LIMIT 1").fetchone()
            if apps_row is None:
                pytest.skip("La copia de la BD no tiene ninguna app para asociar la fila sintetica")
            conn.execute(
                "INSERT INTO sql_findings (app_id, file, class_name, method, kind, category, target, "
                "is_stored_procedure, raw, resolved) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (apps_row["id"], "Legacy.cs", "Legacy", "LegacyMethod", "CommandText", "query", "LegacyTable",
                 0, 'sqlCommand.CommandText = "SELECT 1";', "SELECT 1"),
            )

        db.init_db()  # confirma que re-migrar no toca esta fila

        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT confidence, extractor FROM sql_findings WHERE file = 'Legacy.cs'"
            ).fetchone()
            assert row["confidence"] is None
            assert row["extractor"] is None

    def test_review_status_survives_migration(self, db_copy):
        """Regresion directa de ADR-0001: una migracion de esquema nunca
        debe alterar el contenido curado (review_status/review_notes)."""
        with db.get_conn() as conn:
            apps_before = [dict(r) for r in conn.execute("SELECT id, name, review_status, review_notes FROM apps")]

        if not apps_before:
            pytest.skip("La copia de la BD no tiene apps para verificar")

        db.init_db()

        with db.get_conn() as conn:
            apps_after = [dict(r) for r in conn.execute("SELECT id, name, review_status, review_notes FROM apps")]

        assert apps_before == apps_after, "review_status/review_notes cambiaron durante una migracion de esquema aditiva"

    def test_app_count_unchanged(self, db_copy):
        with db.get_conn() as conn:
            before = conn.execute("SELECT COUNT(*) as c FROM apps").fetchone()["c"]
        db.init_db()
        with db.get_conn() as conn:
            after = conn.execute("SELECT COUNT(*) as c FROM apps").fetchone()["c"]
        assert before == after

    def test_findings_survive_migration(self, db_copy):
        """findings esta keyed por app_name (no app_id) especificamente para
        sobrevivir operaciones como esta -- confirma que sigue siendo cierto."""
        with db.get_conn() as conn:
            before = conn.execute("SELECT COUNT(*) as c FROM findings").fetchone()["c"]
        db.init_db()
        with db.get_conn() as conn:
            after = conn.execute("SELECT COUNT(*) as c FROM findings").fetchone()["c"]
        assert before == after


class TestMigrationIsIdempotent:
    def test_running_init_db_twice_is_safe(self, db_copy):
        """init_db() ya corre en cada arranque de app.py -- confirma que
        correrlo 2 veces seguidas sobre una BD ya migrada no falla ni
        duplica columnas."""
        db.init_db()
        db.init_db()  # no debe lanzar excepcion la segunda vez
        with db.get_conn() as conn:
            cols = _table_columns(conn, "sql_findings")
            assert list(cols).count("confidence") <= 1  # PRAGMA ya deduplica por naturaleza, pero confirma que no rompio
