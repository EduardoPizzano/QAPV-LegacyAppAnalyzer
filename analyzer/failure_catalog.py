"""Failure Cause Catalog -- catalogo centralizado y cerrado de causas de
no-resolucion (VALIDATION_FRAMEWORK.md seccion 5), mismo patron ya usado
para FINDING_STATUSES/REVIEW_STATUSES en analyzer/db.py. Ninguna causa se
escribe como texto libre disperso en distintos modulos -- todo mensaje que
hoy sale de report.py/enrich.py debera, en una fase futura, provenir de una
sola entrada de este catalogo.

Fase 1: este catalogo existe y esta probado en aislamiento. NADA en
enrich.py/report.py/extract.py lo usa todavia -- _short_error() de
enrich.py y el mensaje generico de report.py:50 siguen exactamente igual
(ver tests/test_characterization.py: TestEnrichGenericConnectionErrorMessage,
TestReportGenericQueryMessage, que documentan ese comportamiento actual como
la foto de "antes")."""

from dataclasses import dataclass

# Mismo vocabulario de severidad que analyzer/db.py: FINDING_SEVERITIES --
# no se inventa una escala nueva.
VALID_SEVERITIES = ("critica", "alta", "media", "info")


@dataclass(frozen=True)
class FailureReason:
    code: str
    category: str  # "connection" | "sql" | "integration" | "unknown"
    description: str  # para el desarrollador/auditor
    user_message_template: str  # para la UI -- puede tener placeholders, ej. "{server}"
    severity: str  # una de VALID_SEVERITIES
    recommended_action: str


FAILURE_CATALOG: dict[str, FailureReason] = {
    "DYNAMIC_SQL": FailureReason(
        code="DYNAMIC_SQL",
        category="sql",
        description=(
            "El SQL se arma dinamicamente (StringBuilder, concatenacion "
            "multi-metodo) y no se pudo reconstruir el texto completo de "
            "forma estatica."
        ),
        user_message_template="SQL armado dinamicamente -- no se pudo determinar el texto completo.",
        severity="media",
        recommended_action="Revisar el codigo manualmente para confirmar el texto real de la consulta.",
    ),
    "REFLECTION": FailureReason(
        code="REFLECTION",
        category="integration",
        description=(
            "El codigo invoca miembros via Reflection (MethodInfo.Invoke / "
            "Activator.CreateInstance) -- el comportamiento real depende de "
            "resolucion en tiempo de ejecucion."
        ),
        user_message_template="Se detecto invocacion via Reflection -- el analisis estatico puede estar incompleto.",
        severity="media",
        recommended_action="Confirmar manualmente que metodos/tipos se invocan en tiempo de ejecucion.",
    ),
    "SERVER_UNAVAILABLE": FailureReason(
        code="SERVER_UNAVAILABLE",
        category="connection",
        description="El servidor de base de datos no respondio (caido, decomisionado, o problema de red).",
        user_message_template="Servidor {server} no disponible -- no se pudo conectar.",
        severity="alta",
        recommended_action="Confirmar con infraestructura si el servidor sigue activo, o actualizar la connection string al reemplazo conocido.",
    ),
    "UNRESOLVED_VARIABLE": FailureReason(
        code="UNRESOLVED_VARIABLE",
        category="sql",
        description=(
            "La variable que alimenta el SQL/connection string no se pudo "
            "resolver (declarada fuera del alcance de busqueda actual, ej. "
            "campo de clase en vez de variable local)."
        ),
        user_message_template="No se pudo resolver el valor de la variable -- declarada fuera del metodo actual.",
        severity="alta",
        recommended_action="Revisar el codigo fuente manualmente para confirmar el valor real.",
    ),
    "MISSING_SOURCE": FailureReason(
        code="MISSING_SOURCE",
        category="unknown",
        description=(
            "No se encontro ningun archivo fuente que declare esta "
            "configuracion (Settings.cs/app.config ausentes o no "
            "decompilados)."
        ),
        user_message_template="No se encontro la fuente de esta configuracion.",
        severity="info",
        recommended_action="Confirmar que la decompilacion incluyo todos los ensamblados relevantes (companion assemblies).",
    ),
    "UNKNOWN": FailureReason(
        code="UNKNOWN",
        category="unknown",
        description="No se pudo determinar una causa mas especifica con la evidencia disponible.",
        user_message_template="Causa no determinada.",
        severity="info",
        recommended_action="Revisar manualmente -- este es el residual honesto, no una clasificacion real.",
    ),
}


def get_failure_reason(code: str) -> FailureReason:
    """Nunca lanza excepcion para un codigo desconocido -- cae al residual
    honesto UNKNOWN en vez de tumbar el analisis."""
    return FAILURE_CATALOG.get(code, FAILURE_CATALOG["UNKNOWN"])
