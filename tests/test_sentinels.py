"""Tests centinela (KNOWN_LIMITATIONS.md, patrones P3).

A diferencia de test_characterization.py, estos NUNCA fallan el build --
solo advierten (pytest.warns / captured warning) si un patron hoy ausente
del portafolio aparece en una app nueva analizada. La primera vez que
ocurra hay que promover ese patron de "P3, no implementar" a una fase real
de IMPLEMENTATION_PLAN.md, no descubrirlo por accidente meses despues en
una revision manual.

Corren contra decompiled/ completo (no contra los fixtures congelados de
tests/fixtures/), asi que se saltan solos (skip) si esa carpeta no existe
en la maquina donde corre pytest -- decompiled/ no esta en git (ver
.gitignore) y no debe ser un prerequisito para correr el resto de la suite.
"""

import warnings
from pathlib import Path

import pytest

DECOMPILED_DIR = Path(__file__).parent.parent / "decompiled"

# patron de texto -> nota (referencia a KNOWN_LIMITATIONS.md)
SENTINEL_PATTERNS = {
    "TableAdapter": "L13 -- DataSet Designer, confirmado ausente del portafolio en la auditoria de 2026-08",
    "DbContext": "L12 -- Entity Framework, confirmado ausente",
    ".wsdl": "L20 -- SOAP/WCF, confirmado ausente",
    "RabbitMQ": "L20 -- confirmado ausente",
    "System.ServiceModel": "L20 -- SOAP/WCF, confirmado ausente",
    "System.ServiceProcess": "L20 -- Windows Services, confirmado ausente",
    "CrystalDecisions": "L20 -- Crystal Reports, confirmado ausente",
    "System.Data.Odbc": "L7 -- ODBC DSN, confirmado ausente",
    "System.Messaging": "L20 -- MSMQ, confirmado ausente (unico hit real fue un falso positivo de SnackbarMessageQueue)",
    "FtpWebRequest": "L20 -- FTP, confirmado ausente",
}


def _grep_portfolio(pattern: str) -> list[str]:
    hits = []
    for cs_file in DECOMPILED_DIR.rglob("*.cs"):
        try:
            if pattern in cs_file.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(cs_file.relative_to(DECOMPILED_DIR)))
        except OSError:
            continue
    return hits


@pytest.mark.skipif(not DECOMPILED_DIR.is_dir(), reason="decompiled/ no existe en esta maquina (no versionado, ver .gitignore)")
def test_sentinel_patterns_still_absent_or_only_in_vendored_code():
    """Informativo, nunca falla -- ver docstring del modulo."""
    findings = {}
    for pattern, note in SENTINEL_PATTERNS.items():
        hits = _grep_portfolio(pattern)
        # Las librerias vendorizadas ya conocidas (iText, BouncyCastle,
        # OpenCvSharp, BenchmarkDotNet, Roslyn) pueden mencionar estos
        # patrones en su propio codigo interno sin que sea una app real
        # usandolos -- eso ya se filtro a mano en la auditoria original.
        # Aqui no se filtra (seria repetir esa auditoria en cada test run),
        # solo se advierte con la ruta completa para que quien lo lea decida.
        if hits:
            findings[pattern] = (note, hits[:5])

    if findings:
        msg = "\n".join(
            f"  - '{pattern}' ({note}): {len(hits)} archivo(s), ej. {hits}"
            for pattern, (note, hits) in findings.items()
        )
        warnings.warn(
            f"Patrones centinela encontrados en decompiled/ (revisar si alguno es real, "
            f"no vendorizado, antes de promover a una fase de IMPLEMENTATION_PLAN.md):\n{msg}"
        )
