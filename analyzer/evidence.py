"""Evidence -- el "como lo sabemos" de un hallazgo (VALIDATION_FRAMEWORK.md
seccion 0). Complementa, NUNCA reemplaza, los campos que ya existen en
SqlFinding/SettingEntry/LocalIOFinding (file/class_name/method/target) --
esos siguen siendo consumidos tal cual por report.py/export_office.py.

Regla arquitectonica del proyecto: Evidence nace en el momento exacto de la
extraccion (dentro de extract.py), nunca se reconstruye despues a partir de
un reporte ya generado. En Fase 1 esto es solo el contenedor -- ningun
extractor construye todavia un Evidence con datos reales (eso es Fase 2+);
todo SqlFinding/SettingEntry/LocalIOFinding de hoy recibe el Evidence() por
defecto, que documenta explicitamente "no instrumentado todavia" en vez de
inventar un valor.
"""

from dataclasses import dataclass

from .__version__ import ANALYZER_VERSION


@dataclass(frozen=True)
class Evidence:
    source_file: str | None = None
    line_number: int | None = None
    snippet: str | None = None
    extractor: str = "UNKNOWN"
    pattern: str | None = None
    confidence: int = 20
    analyzer_version: str = ANALYZER_VERSION
    created_at: str | None = None
