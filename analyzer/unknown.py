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
    # Incremento Huella de Datos (2026-08-18, analyzer/server_resolution.py):
    # dos estados especificos de resolucion de CONEXION (servidor/BD), no de
    # contenido SQL -- se agregan aqui (no en un enum paralelo) para que
    # "estado de resolucion" siga significando una sola cosa en todo el
    # proyecto, igual que confidence.py hace con su tabla centralizada.
    "unresolved_ambiguous_conditional",  # 2+ conexiones candidatas (ej. ternario), cual aplica depende de una condicion no evaluada
    "unresolved_no_source_file",         # el archivo fuente ya decompilado no se pudo localizar/leer (no es un gap de patron, es un problema de entorno)
    # Incremento Mapa de Flujo de Datos (2026-08-19, analyzer/data_flow.py):
    # se confirmo la operacion (INSERT/UPDATE/SELECT/DELETE) pero no fue
    # posible reconstruir la lista de columnas involucradas con confianza
    # suficiente (ej. SELECT *, SQL dinamico real, o ni result_columns ni
    # el regex literal aplican) -- nunca se infiere una columna.
    "unresolved_no_columns",
    # Incremento Flujo de Aplicacion - Navigation Discovery (2026-08-20,
    # analyzer/app_navigation.py): un Show()/ShowDialog() se observo
    # realmente en el codigo, pero el TARGET de esa navegacion no se pudo
    # resolver con la certeza suficiente -- dos causas distintas, dos
    # estados distintos (nunca se colapsan en uno solo):
    "unresolved_navigation_target_unknown",       # el tipo de la variable mostrada no se pudo determinar dentro del mismo metodo (ej. proviene de una factory/metodo, no de "new TypeName(")
    "unresolved_target_not_a_known_screen",        # el tipo SI se determino, pero esa clase no fue clasificada como Form/Window por Application Structure Discovery
    # Incremento Flujo de Aplicacion - Event Wiring + Intra-Class Call Flow
    # (2026-08-20, analyzer/app_interactions.py): cuatro estados nuevos,
    # cada uno para una causa distinta de incertidumbre -- nunca se
    # colapsan en uno solo (wpf_event_wiring_not_observable_in_cs, ya
    # definido arriba, se reutiliza tal cual para el caso WPF).
    "unresolved_event_handler_unknown",     # existe "control.Evento += ..." real, pero el handler concreto no se pudo determinar con seguridad (ej. lambda, wrapper indirecto)
    "unresolved_call_target_unknown",       # llamada sin receptor observada, pero el nombre no coincide con ningun metodo de la clase actual ni de su clase base directa
    "unresolved_call_target_ambiguous",     # el nombre coincide con 2+ metodos de la misma clase (sobrecarga real) -- sin conteo de argumentos no se puede saber cual
    "unresolved_call_target_inherited",     # el nombre no esta en la clase actual pero SI en su clase base directa (un solo salto, sin resolucion de herencia completa)
    # Incremento Flujo de Aplicacion - Data Flow Integration (2026-08-20,
    # analyzer/app_data_flow.py):
    "unresolved_method_sql_mapping",   # un SqlFinding no se pudo atribuir con seguridad a un MethodInfo (0 candidatos por archivo, o 2+ con firma distinta -- sobrecarga real)
    # Incremento Flujo de Aplicacion - Screen Surface Discovery (2026-08-21,
    # analyzer/app_controls.py):
    "unresolved_control_type_unknown",  # se observo instanciacion real de un control, pero su tipo no pertenece al catalogo curado -- se sabe que existe, no que tipo concreto es
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
