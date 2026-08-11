"""Regresion de un bug real: GeoStatsInter quedo analizado dos veces --
una vez desde "/analyze" apuntando directo al .exe (nombre "GeoStatsInter"),
otra vez desde "/discover" apuntando a la carpeta raiz del proyecto (nombre
de batch "GeoStatsInter/GeoStatsInter", mismo source_path). save_analysis()
solo hacia upsert por `name`, asi que las dos analisis del MISMO ejecutable
terminaron en DOS filas distintas de `apps` en vez de que la segunda
reemplazara a la primera. Ahora tambien busca por source_path antes de
insertar, y conserva el nombre ya existente para no renombrar una app ya
revisada solo porque la segunda corrida uso un nombre distinto.

Corre siempre sobre una BD temporal vacia (via db.init_db()), nunca sobre
qapv_analyzer.db real -- mismo patron que test_schema_migration.py."""

import pytest

from analyzer import db
from analyzer.techstack import TechStack


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["Console/Service"], db_drivers=["System.Data.SqlClient"])


def _save(name, source_path):
    return db.save_analysis(name, source_path, _tech(), [], [], [], [])


class TestUpsertBySourcePathPreventsDuplicateRows:
    def test_reanalyzing_same_exe_under_a_different_name_updates_the_same_row(self, temp_db):
        """No exige que el id se preserve (DELETE+INSERT nunca lo garantizo,
        ni antes de este fix) -- lo que importa es que quede UNA sola fila,
        no dos, y que conserve el nombre original."""
        exe = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\GeoStatsInter\bin\Debug\GeoStatsInter.exe"

        _save("GeoStatsInter", exe)
        _save("GeoStatsInter/GeoStatsInter", exe)

        with db.get_conn() as conn:
            rows = conn.execute("SELECT id, name FROM apps WHERE source_path = ?", (exe,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "GeoStatsInter"  # conserva el nombre original, no el del batch

    def test_review_notes_survive_the_renamed_reanalysis(self, temp_db):
        exe = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\GeoStatsInter\bin\Debug\GeoStatsInter.exe"
        app_id = _save("GeoStatsInter", exe)
        db.set_review(app_id, "logica_revisada", "Revision real ya hecha.")

        _save("GeoStatsInter/GeoStatsInter", exe)

        with db.get_conn() as conn:
            row = conn.execute("SELECT review_status, review_notes FROM apps WHERE source_path = ?", (exe,)).fetchone()
        assert row["review_status"] == "logica_revisada"
        assert row["review_notes"] == "Revision real ya hecha."

    def test_different_apps_at_different_paths_stay_separate(self, temp_db):
        _save("BINNA", r"\\naamrt-qcs25\...\BINNA\bin\Debug\Exfo_CLEAN_RL1.exe")
        _save("SANA", r"\\naamrt-qcs25\...\SANA\bin\Debug\Exfo_CLEAN_RL1.exe")

        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM apps").fetchone()["c"]
        assert count == 2

    def test_same_name_different_path_still_upserts_by_name(self, temp_db):
        """Comportamiento preexistente que no debe romperse: re-analizar bajo
        el mismo nombre (p.ej. el .exe se reconstruyo en una ruta nueva)
        sigue reemplazando la fila anterior, no duplicandola."""
        _save("SomeApp", r"\\server\v1\SomeApp.exe")
        _save("SomeApp", r"\\server\v2\SomeApp.exe")

        with db.get_conn() as conn:
            rows = conn.execute("SELECT source_path FROM apps WHERE name = ?", ("SomeApp",)).fetchall()
        assert len(rows) == 1
        assert rows[0]["source_path"] == r"\\server\v2\SomeApp.exe"
