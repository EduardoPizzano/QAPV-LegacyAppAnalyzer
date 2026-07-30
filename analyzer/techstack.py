"""Infers the technology/language/UI-framework/DB-driver stack of a decompiled app."""

import re
from dataclasses import dataclass, field
from pathlib import Path

TARGET_FRAMEWORK = re.compile(r"<TargetFramework>([^<]+)</TargetFramework>")
TARGET_FRAMEWORK_VERSION = re.compile(r"<TargetFrameworkVersion>([^<]+)</TargetFrameworkVersion>")

UI_MARKERS = {
    "WinForms": re.compile(r"using System\.Windows\.Forms;|:\s*Form\b|:\s*UserControl\b.*System\.Windows\.Forms"),
    "WPF": re.compile(r"using System\.Windows\.Controls;|InitializeComponent\(\).*\.xaml|:\s*Window\b"),
    "Console/Service": re.compile(r"static\s+(?:async\s+)?(?:void|Task)\s+Main\s*\("),
}

DB_DRIVER_MARKERS = {
    "System.Data.SqlClient (SQL Server)": re.compile(r"using System\.Data\.SqlClient;"),
    "Microsoft.Data.SqlClient (SQL Server)": re.compile(r"using Microsoft\.Data\.SqlClient;"),
    "Oracle.ManagedDataAccess (Oracle)": re.compile(r"using Oracle\.ManagedDataAccess"),
    "Oracle.DataAccess (Oracle, unmanaged)": re.compile(r"using Oracle\.DataAccess"),
}


@dataclass
class TechStack:
    language: str = "C#"
    dotnet_target: str = "(no detectado)"
    ui_framework: list[str] = field(default_factory=list)
    db_drivers: list[str] = field(default_factory=list)


def detect(root: Path) -> TechStack:
    stack = TechStack()

    for csproj in root.rglob("*.csproj"):
        text = csproj.read_text(encoding="utf-8", errors="ignore")
        m = TARGET_FRAMEWORK.search(text) or TARGET_FRAMEWORK_VERSION.search(text)
        if m:
            stack.dotnet_target = m.group(1)
            break

    ui_found = set()
    driver_found = set()
    for cs_file in root.rglob("*.cs"):
        text = cs_file.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in UI_MARKERS.items():
            if label not in ui_found and pattern.search(text):
                ui_found.add(label)
        for label, pattern in DB_DRIVER_MARKERS.items():
            if label not in driver_found and pattern.search(text):
                driver_found.add(label)

    # Prefer the more specific UI framework over the generic "has a Main method" signal.
    if "WinForms" in ui_found or "WPF" in ui_found:
        ui_found.discard("Console/Service")

    stack.ui_framework = sorted(ui_found) or ["(no detectado)"]
    stack.db_drivers = sorted(driver_found) or ["(ninguno detectado)"]
    return stack
