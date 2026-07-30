"""Orchestrates decompile -> extract -> techstack -> security for one app.
Used by both the CLI (main.py) and the web app."""

from dataclasses import dataclass
from pathlib import Path

from . import security, techstack
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


def run_analysis(assembly_path: Path, app_name: str | None = None) -> AnalysisResult:
    assembly_path = Path(assembly_path)
    app_name = app_name or assembly_path.stem
    output_dir = DECOMPILED_DIR / app_name

    decompile(assembly_path, output_dir)

    companions = []
    for companion in find_companion_assemblies(assembly_path):
        try:
            decompile(companion, output_dir)
            companions.append(companion.name)
        except DecompileError:
            pass  # skip a companion that fails rather than aborting the whole analysis

    settings = find_settings(output_dir)
    sql_findings, io_findings = scan_project(output_dir)
    tech = techstack.detect(output_dir)
    flags = security.check_settings(settings) + security.check_findings(sql_findings)

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
    )
