"""Orchestrates decompile -> extract -> techstack -> security for one app.
Used by both the CLI (main.py) and the web app."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import security, techstack
from .activity import ActivityEvidence, build_date as _build_date, detect_activity_evidence
from .classification import (
    classify_decompiled_assemblies,
    third_party_folder_names,
    top_level_dir_names,
)
from .decompile import DecompileError, decompile, find_companion_assemblies
from .extract import LocalIOFinding, SettingEntry, SqlFinding, find_settings, scan_project
from .security import SecurityFlag
from .techstack import TechStack

BASE_DIR = Path(__file__).parent.parent
DECOMPILED_DIR = BASE_DIR / "decompiled"


@dataclass
class AnalysisResult:
    app_name: str
    source_path: str
    output_dir: Path
    tech: TechStack
    settings: list[SettingEntry]
    sql_findings: list[SqlFinding]
    io_findings: list[LocalIOFinding]
    security_flags: list[SecurityFlag]
    companion_assemblies: list[str]
    # Incremento Lifecycle (2026-08-13): evidencia de ciclo de vida de la app,
    # deliberadamente separada de los findings de arriba (extract.py no se
    # toco para esto). build_date es un PROXY (mtime del ensamblado), no una
    # fecha de compilacion verificada -- ver analyzer/activity.py para la
    # investigacion completa de por que. activity es best-effort: ausencia de
    # evidencia nunca implica "no se uso".
    build_date: str | None = None
    activity: ActivityEvidence = field(default_factory=ActivityEvidence)


def run_analysis(assembly_path: Path, app_name: str | None = None) -> AnalysisResult:
    assembly_path = Path(assembly_path)
    app_name = app_name or assembly_path.stem
    output_dir = DECOMPILED_DIR / app_name

    # Empezar en limpio: si una corrida anterior (de este mismo app_name, o de
    # un nombre distinto que por colision de rutas terminaba escribiendo aqui
    # mismo -- ver _batch_name() en app.py) dejo archivos en output_dir, el
    # truco de "carpetas antes/menos despues" de abajo solo detecta carpetas
    # nuevas, no archivos sueltos preexistentes -- esos se cuelan en
    # find_settings/scan_project como si fueran parte de ESTA corrida,
    # duplicando hallazgos (bug real, ver GeoStatsInter 2026-08-11).
    shutil.rmtree(output_dir, ignore_errors=True)

    # Evidencia de secuencia, no de nombre (DISENO_INCREMENTO_3_CLASIFICACION.md,
    # Decision 1): un mismo assembly se fragmenta en varias carpetas de nivel
    # superior, una por cada namespace distinto que contiene (confirmado con
    # evidencia real -- decompilar solo EtiquetasRH.exe produjo un unico
    # EtiquetasRH.csproj pero seis carpetas hermanas). Por eso se comparan
    # las carpetas que existen justo antes y justo despues de la UNICA
    # llamada a decompile() para el assembly de entrada -- la diferencia es,
    # por construccion, todo lo que pertenece a esa app, sin asumir que su
    # nombre coincide con assembly_path.stem.
    before_main = top_level_dir_names(output_dir)
    decompile(assembly_path, output_dir)
    application_folder_names = top_level_dir_names(output_dir) - before_main

    companions = []
    for companion in find_companion_assemblies(assembly_path):
        try:
            decompile(companion, output_dir)
            companions.append(companion.name)
        except DecompileError:
            pass  # skip a companion that fails rather than aborting the whole analysis

    classifications = classify_decompiled_assemblies(output_dir, application_folder_names)
    skip_top_level = third_party_folder_names(classifications)

    settings = find_settings(output_dir, skip_top_level=skip_top_level)
    sql_findings, io_findings = scan_project(output_dir, skip_top_level=skip_top_level)
    tech = techstack.detect(output_dir)
    flags = security.check_settings(settings) + security.check_findings(sql_findings)

    # Incremento Lifecycle: se calcula sobre assembly_path (la ruta real en
    # el share), NUNCA sobre output_dir -- output_dir solo tiene codigo
    # fuente decompilado, jamas logs de runtime reales. Ver activity.py para
    # el porque de cada metodo.
    build_date_value = _build_date(assembly_path)
    activity = detect_activity_evidence(assembly_path)

    return AnalysisResult(
        app_name=app_name,
        source_path=str(assembly_path),
        output_dir=output_dir,
        tech=tech,
        settings=settings,
        sql_findings=sql_findings,
        io_findings=io_findings,
        security_flags=flags,
        companion_assemblies=companions,
        build_date=build_date_value,
        activity=activity,
    )
