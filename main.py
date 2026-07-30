"""
QAPV Legacy App Analyzer — CLI
--------------------------------
Decompila un .exe/.dll con ilspycmd y extrae connection strings + queries SQL/Oracle
+ accesos a archivos/impresoras/procesos/red + stack tecnologico + alertas de seguridad.

Uso:
    python main.py "\\ruta\\a\\LaApp.exe"
    python main.py "\\ruta\\a\\LaApp.exe" --name "NombrePersonalizado"
    python main.py "\\ruta\\a\\LaApp.exe" --save-db     (tambien guarda en la base acumulativa)
"""

import argparse
import sys
from pathlib import Path

from analyzer.decompile import DecompileError
from analyzer.pipeline import run_analysis
from analyzer.report import render

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analiza una app legacy QAPV: decompila + extrae SQL.")
    parser.add_argument("assembly", help="Ruta al .exe o .dll a analizar")
    parser.add_argument("--name", help="Nombre a usar para las carpetas/reportes de salida", default=None)
    parser.add_argument("--save-db", action="store_true", help="Tambien guarda el resultado en la base acumulativa")
    args = parser.parse_args()

    print(f"[1/3] Decompilando '{Path(args.assembly).name}' ...")
    try:
        result = run_analysis(args.assembly, args.name)
    except DecompileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(
        f"[2/3] Extraidos: {len(result.settings)} settings, {len(result.sql_findings)} hallazgos SQL, "
        f"{len(result.io_findings)} accesos a archivos/impresoras/procesos/red, "
        f"{len(result.security_flags)} alertas de seguridad."
    )

    report_text = render(
        result.app_name, result.tech, result.settings, result.sql_findings,
        result.io_findings, result.security_flags, result.companion_assemblies,
    )
    report_path = REPORTS_DIR / f"{result.app_name}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    if args.save_db:
        from analyzer.db import init_db, save_analysis
        init_db()
        save_analysis(
            result.app_name, result.source_path, result.tech, result.settings,
            result.sql_findings, result.io_findings, result.security_flags,
            result.companion_assemblies,
        )
        print("[3/3] Guardado en la base de datos acumulativa (qapv_analyzer.db).")

    print(f"\nListo. Reporte en: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
