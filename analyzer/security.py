"""Flags common security smells found repeatedly across the legacy QAPV apps."""

import re
from dataclasses import dataclass

from .extract import SettingEntry, SqlFinding

PASSWORD_IN_CONN = re.compile(r"(?i)(password|pwd)\s*=\s*([^;]+)")
CONCAT_SQL = re.compile(r'"\s*\+\s*\w|\+\s*"')
GARBAGE_PLACEHOLDER = re.compile(r"^[a-z]{4,10}$")  # e.g. "dfgsdf" seen in DataTransfer's CXOra


@dataclass
class SecurityFlag:
    severity: str  # "alta" | "media" | "info"
    description: str
    location: str


def check_settings(settings: list[SettingEntry]) -> list[SecurityFlag]:
    flags: list[SecurityFlag] = []
    for s in settings:
        if s.is_connection_string:
            m = PASSWORD_IN_CONN.search(s.default_value)
            if m and m.group(2).strip():
                flags.append(
                    SecurityFlag(
                        severity="alta",
                        description=f"Credenciales en texto plano en connection string '{s.name}' "
                                    f"(password='{m.group(2).strip()}')",
                        location=s.source_file,
                    )
                )
            if GARBAGE_PLACEHOLDER.match(s.default_value.strip()):
                flags.append(
                    SecurityFlag(
                        severity="info",
                        description=f"Setting '{s.name}' marcado como ConnectionString pero su valor "
                                     f"por defecto parece un placeholder sin configurar ('{s.default_value}') "
                                     f"— probablemente no se usa en produccion, verificar.",
                        location=s.source_file,
                    )
                )
    return flags


def check_findings(findings: list[SqlFinding]) -> list[SecurityFlag]:
    flags: list[SecurityFlag] = []
    for f in findings:
        text = f.resolved or f.raw
        if f.category == "query" and CONCAT_SQL.search(text) and "@" not in text:
            flags.append(
                SecurityFlag(
                    severity="media",
                    description=f"Posible SQL injection: query armada por concatenacion de strings "
                                 f"sin parametros en `{f.class_name}.{f.method}`",
                    location=f"{f.file} -> {f.class_name}.{f.method}",
                )
            )
    return flags
