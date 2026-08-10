"""Unknown -- decision conceptual de Fase 1 (Paso 4). NO es un finding
nuevo, NO duplica security_flags/findings: es la proyeccion visible de un
hallazgo cuyo estado de resolucion (RESOLUTION_STATUSES) quedo distinto de
"resolved". Ver VALIDATION_FRAMEWORK.md secciones 2 y 4, y
ARCHITECTURE_REVIEW.md seccion 7 (por que se disena con el mismo vocabulario
de severidad que `findings`/`security_flags` en vez de una forma aislada).

Decision de representacion (pedida explicitamente en Paso 4):
- El "estado de conocimiento incompleto" en si se representa como un
  ENUM/STATUS (RESOLUTION_STATUSES) -- es un atributo del hallazgo, no una
  entidad separada con vida propia.
- Cuando ese status es distinto de "resolved"/"not_applicable", existe un
  Unknown -- una fila derivada (ver UnknownRecord) que resume ese hallazgo
  para quien lee el reporte, sin repetir toda la evidencia del finding
  original.

Fase 1: este modulo define la FORMA. Nada la usa todavia -- ningun campo
`resolution_status` se agrego a SqlFinding/SettingEntry en esta fase (no fue
pedido), y la tabla `unknowns` (ver analyzer/db.py) se crea vacia. La
generacion automatica de Unknowns reales ocurre en una fase futura, cuando
extract.py sea instrumentado para asignar resolution_status de verdad."""

from dataclasses import dataclass

RESOLUTION_STATUSES = (
    "resolved",                    # texto/valor completo obtenido
    "unresolved_dynamic_sql",       # StringBuilder u otro ensamblado no lineal
    "unresolved_out_of_scope",      # variable resuelta fuera del metodo actual (ej. campo de clase)
    "unresolved_no_literal",        # causa no identificada especificamente (residual honesto)
    "not_applicable",               # ej. un trigger de I/O no tiene "resolucion" en el mismo sentido que un SQL
)


@dataclass(frozen=True)
class UnknownRecord:
    """Forma conceptual de una fila de la futura tabla `unknowns` -- ver
    VALIDATION_FRAMEWORK.md seccion 4.1 para el DDL. No se persiste desde
    este modulo; esto es la definicion de forma, no un repositorio."""

    app_name: str
    category: str          # 'stored_procedure' | 'query' | 'connection' | 'reflection' | 'configuration' | ...
    reason_code: str        # codigo de analyzer.failure_catalog.FAILURE_CATALOG
    impact: str              # interpolado desde FailureReason.user_message_template, nunca escrito a mano
    evidence_file: str | None = None
    evidence_class: str | None = None
    evidence_method: str | None = None
    evidence_line: int | None = None
    suggested_action: str | None = None  # viene de FailureReason.recommended_action
    priority: str = "media"               # derivado de FailureReason.severity, nunca manual
