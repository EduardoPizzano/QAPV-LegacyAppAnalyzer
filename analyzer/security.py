"""Flags common security smells found repeatedly across the legacy QAPV apps."""

import re
from dataclasses import dataclass

from .extract import SettingEntry, SqlFinding
from .i18n import _

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
                        description=_("Credenciales en texto plano en connection string '%(name)s' (password='%(pwd)s')")
                        % {"name": s.name, "pwd": m.group(2).strip()},
                        location=s.source_file,
                    )
                )
            if GARBAGE_PLACEHOLDER.match(s.default_value.strip()):
                flags.append(
                    SecurityFlag(
                        severity="info",
                        description=_(
                            "Setting '%(name)s' marcado como ConnectionString pero su valor por defecto "
                            "parece un placeholder sin configurar ('%(value)s') — probablemente no se usa "
                            "en produccion, verificar."
                        )
                        % {"name": s.name, "value": s.default_value},
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
                    description=_(
                        "Posible SQL injection: query armada por concatenacion de strings "
                        "sin parametros en `%(location)s`"
                    )
                    % {"location": f"{f.class_name}.{f.method}"},
                    location=f"{f.file} -> {f.class_name}.{f.method}",
                )
            )
    return flags
