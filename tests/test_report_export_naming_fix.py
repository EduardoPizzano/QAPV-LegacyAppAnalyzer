"""Regresion del bug real de reporte/export (2026-08-11, GeoStatsInter;
reproducido de forma controlada 2026-08-13 con RL1PolInterfaceLocalMultiple):
tanto app.py::_analyze_and_save() como main.py::main() escribian el .md
usando result.app_name -- el nombre solo PROPUESTO para esa corrida -- ANTES
de llamar a save_analysis(). Cuando source_path ya coincidia con una fila
existente bajo otro nombre, save_analysis() conservaba silenciosamente el
nombre viejo (ver analyzer/db.py y test_save_analysis_dedup.py), pero el
reporte ya se habia escrito en la ruta del nombre propuesto -- un .md huerfano
que nunca se actualizaba en corridas futuras, mientras la fila real de la BD
se quedaba sin su reporte reflejado.

La correccion (2026-08-13) invierte el orden: save_analysis() PRIMERO, leer
el nombre que la BD realmente conservo via get_app(), y SOLO ENTONCES
renderizar/escribir el .md con ese nombre final. Estos son los 2 tests
obligatorios que verifican esa correccion en ambos puntos de entrada."""

from pathlib import Path

import pytest

from analyzer import db
from analyzer.pipeline import AnalysisResult
from analyzer.techstack import TechStack

EXE = r"\\server\Proyecto\bin\Debug\Proyecto.exe"


def _canned_result(proposed_name: str) -> AnalysisResult:
    return AnalysisResult(
        app_name=proposed_name,
        source_path=EXE,
        output_dir=Path("unused"),
        tech=TechStack(),
        settings=[],
        sql_findings=[],
        io_findings=[],
        security_flags=[],
        companion_assemblies=[],
    )


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return tmp_path


class TestAppPyWritesTheMdUnderTheFinalDbName:
    """Cubre app.py::_analyze_and_save()."""

    def test_first_analysis_writes_md_under_the_proposed_name(self, temp_db, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module, "REPORTS_DIR", temp_db / "reports")
        monkeypatch.setattr(app_module, "run_analysis", lambda assembly, name: _canned_result(name))

        outcome = app_module._analyze_and_save(Path(EXE), "Proyecto")

        assert outcome["app_name"] == "Proyecto"
        assert (temp_db / "reports" / "Proyecto.md").is_file()

    def test_reanalysis_under_a_different_proposed_name_updates_the_original_md_not_a_new_orphaned_one(
        self, temp_db, monkeypatch
    ):
        """El escenario exacto del bug: la misma app se analiza primero via
        '/analyze' directo al .exe (nombre 'Proyecto'), luego via '/discover'
        con el nombre de batch 'Root/Proyecto' -- mismo source_path.
        save_analysis() conserva 'Proyecto' (el nombre original); el .md
        debe seguir viviendo (y actualizarse) en Proyecto.md, nunca crear
        Root/Proyecto.md huerfano."""
        import app as app_module

        monkeypatch.setattr(app_module, "REPORTS_DIR", temp_db / "reports")
        monkeypatch.setattr(app_module, "run_analysis", lambda assembly, name: _canned_result(name))

        app_module._analyze_and_save(Path(EXE), "Proyecto")
        outcome = app_module._analyze_and_save(Path(EXE), "Root/Proyecto")

        assert outcome["app_name"] == "Proyecto"  # save_analysis() conservo el nombre original
        assert (temp_db / "reports" / "Proyecto.md").is_file()
        assert not (temp_db / "reports" / "Root").exists(), (
            "Se creo una carpeta/reporte huerfano para el nombre propuesto en vez de "
            "actualizar el reporte de la fila real"
        )


class TestMainPyWritesTheMdUnderTheFinalDbName:
    """Cubre main.py::main() (con --save-db) -- mismo bug, misma correccion,
    punto de entrada distinto (CLI en vez de Flask)."""

    def test_reanalysis_under_a_different_proposed_name_updates_the_original_md_not_a_new_orphaned_one(
        self, temp_db, monkeypatch
    ):
        import main as main_module

        monkeypatch.setattr(main_module, "REPORTS_DIR", temp_db / "reports")
        monkeypatch.setattr(
            main_module, "run_analysis", lambda assembly, name=None: _canned_result(name or "Proyecto")
        )

        monkeypatch.setattr("sys.argv", ["main.py", EXE, "--name", "Proyecto", "--save-db"])
        assert main_module.main() == 0

        monkeypatch.setattr("sys.argv", ["main.py", EXE, "--name", "Root/Proyecto", "--save-db"])
        assert main_module.main() == 0

        assert (temp_db / "reports" / "Proyecto.md").is_file()
        assert not (temp_db / "reports" / "Root").exists()
