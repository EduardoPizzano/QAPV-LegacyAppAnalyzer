"""Wraps ilspycmd to decompile an assembly into a full project (source tree)."""

import re
import shutil
import subprocess
from pathlib import Path

# Common third-party/framework assembly names found sitting next to these legacy
# apps' .exe in bin\Debug (NuGet packages, .NET Framework itself, UI toolkits).
# Anything NOT matching this is assumed to be an in-house/custom assembly (e.g.
# "ClassLib.dll") that's part of the app's own logic and worth decompiling too —
# file presence (has a .pdb/.xml) isn't a reliable signal here, since plenty of
# third-party packages (MaterialDesignColors, GalaSoft.MvvmLight...) ship their
# own .pdb/.xml right alongside the .dll, same as an in-house library would.
THIRD_PARTY_ASSEMBLY_PATTERN = re.compile(
    r"(?i)^("
    r"System(\..+)?|Microsoft(\..+)?|mscorlib|netstandard|WindowsBase|"
    r"PresentationCore|PresentationFramework|Newtonsoft.*|GalaSoft.*|"
    r"MaterialDesign.*|CommonServiceLocator|Oracle\..*|EntityFramework.*|"
    r"log4net|NLog|Serilog.*|ICSharpCode.*|DevExpress.*|Telerik.*|"
    r"ClosedXML|DocumentFormat\.OpenXml.*|EPPlus|iTextSharp.*|WindowsAPICodePack.*|Costura.*|"
    r"Antlr3.*|Autofac.*|AutoMapper.*|Castle\..*|Dapper|FluentValidation.*|"
    r"Google\..*|Grpc.*|Ionic\.Zip.*|Mono\..*|Polly|protobuf-net|"
    r"RestSharp|StackExchange.*|Unity(\..+)?"
    r")$"
)


class DecompileError(RuntimeError):
    pass

# Folders that never contain the real deployed executable, just build
# intermediates/tooling caches/VCS metadata — safe to skip everywhere.
EXCLUDED_DIR_NAMES = {"obj", ".vs", ".git", "packages"}


def discover_assemblies(root: Path) -> list[Path]:
    """Recursively finds candidate .exe files under root — for solution folders
    that hold several projects side by side (e.g. AFL.Dashboard's Entrega/
    Liberacion/Recibo/Reportes/Scrap modules, each its own bin\\Debug\\*.exe),
    so the user can point at the solution root instead of each project folder
    one at a time."""
    candidates = []
    for exe in root.rglob("*.exe"):
        if exe.stem.lower().endswith(".vshost"):
            continue
        if EXCLUDED_DIR_NAMES & {p.lower() for p in exe.parts}:
            continue
        candidates.append(exe)
    return sorted(candidates)


def project_label(exe: Path) -> str:
    """Best-effort project/module name for grouping discovery results: the
    folder above 'bin' in the usual <Project>\\bin\\Debug\\x.exe layout, or
    just the exe's immediate parent folder if that convention isn't used."""
    for parent in exe.parents:
        if parent.name.lower() == "bin":
            return parent.parent.name
    return exe.parent.name


def find_companion_assemblies(assembly_path: Path) -> list[Path]:
    """Sibling .dll files in the same folder as assembly_path that look like
    in-house code (not a well-known third-party/framework package) — these get
    decompiled alongside the main assembly since legacy apps here often split
    connection strings/business logic into a separate referenced ClassLib."""
    companions = []
    for dll in sorted(assembly_path.parent.glob("*.dll")):
        if dll.resolve() == assembly_path.resolve():
            continue
        if THIRD_PARTY_ASSEMBLY_PATTERN.match(dll.stem):
            continue
        companions.append(dll)
    return companions


def check_ilspycmd_available() -> None:
    if shutil.which("ilspycmd") is None:
        raise DecompileError(
            "No se encontro 'ilspycmd' en el PATH. Instalalo con:\n"
            "  dotnet tool install -g ilspycmd\n"
            "y asegurate de que %USERPROFILE%\\.dotnet\\tools este en tu PATH."
        )


def decompile(assembly_path: Path, output_dir: Path) -> Path:
    """
    Decompila assembly_path como proyecto completo dentro de output_dir.
    Devuelve la ruta de output_dir.
    """
    check_ilspycmd_available()

    if not assembly_path.exists():
        raise DecompileError(f"No existe el archivo: {assembly_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["ilspycmd", "-p", "-o", str(output_dir), str(assembly_path)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise DecompileError(
            f"ilspycmd fallo (codigo {result.returncode}) en {assembly_path}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return output_dir
