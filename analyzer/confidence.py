"""Confidence Engine -- la confianza NUNCA se asigna a mano por hallazgo
(VALIDATION_FRAMEWORK.md seccion 3). Se deriva mecanicamente de UNA tabla
centralizada indexada por el nombre del extractor/patron responsable
(el mismo string que va en Evidence.extractor). Un solo lugar decide, todo
lo demas (report.py, la futura UI, Discovery/Resolution Coverage) consume.

Fase 1: este modulo existe y esta probado en aislamiento, pero NADA en
extract.py lo llama todavia -- eso es Fase 2+, cuando cada punto real de
extraccion se instrumente para pasar su propio nombre de extractor.

Cada entrada documenta por que tiene ese numero -- pedido explicito del
usuario ("cada regla debera estar documentada")."""

# Introspeccion real contra SQL Server -- verificado contra la fuente de
# verdad misma, la confianza mas alta posible.
DB_INTROSPECT_DEFINITION = 100
DB_INTROSPECT_SCHEMA = 100

# Declarado explicitamente en app.config <connectionStrings> -- sin
# ambiguedad de parsing (XML real, no regex).
APP_CONFIG_EXPLICIT_CONNECTION = 98

# Declarado explicitamente via Settings.cs (DefaultSettingValue) -- el
# mecanismo dominante de descubrimiento de conexiones en este portafolio.
SETTINGS_DEFAULT_VALUE = 95

# Literal hardcodeado resuelto DENTRO del mismo metodo (var = "...";
# STRING_VAR_ASSIGN).
HARDCODED_METHOD_LITERAL = 90

# Literal hardcodeado encontrado como CAMPO DE CLASE, fuera del metodo
# actual (KNOWN_LIMITATIONS.md L1, ej. AlmacenDiagnostico) -- un poco menos
# confiable que el caso anterior porque hoy requiere un mecanismo de
# resolucion distinto (Fase 2), no probado en produccion todavia.
SETTINGS_CLASS_LITERAL = 85

# Reconstruccion parcial -- sabemos ALGO pero no todo (ej. target de un
# JOIN parcialmente resuelto).
PARTIAL_RECONSTRUCTION = 80

# Heuristica de regex sobre texto ya resuelto -- funciona bien pero es
# pattern-matching, no verificacion (la mayoria de _classify_sql() hoy).
REGEX_KEYWORD_MATCH = 70

# Inferencia sobre datos incompletos (ej. adivinar una tabla desde un JOIN
# parcialmente resuelto).
INFERRED = 60

# SQL dinamico (StringBuilder, concatenacion multi-metodo) -- sabemos que
# la operacion existe pero no el contenido exacto (KNOWN_LIMITATIONS.md L8).
DYNAMIC_SQL = 40

# Reflection detectado -- el comportamiento real depende de resolucion en
# tiempo de ejecucion, ningun analisis estatico puede saberlo con certeza
# (KNOWN_LIMITATIONS.md L16).
REFLECTION = 40

# Piso de la escala -- nunca 0. Un 0 implicaria "sabemos que esto es falso";
# lo que realmente queremos decir es "no sabemos", que es distinto.
UNKNOWN = 20

# Incremento Lifecycle (2026-08-13, analyzer/activity.py): confianza de la
# evidencia de ULTIMA ACTIVIDAD de una app (no de un hallazgo SQL/IO) --
# reutiliza esta misma escala 0-100 en vez de inventar una paralela
# cualitativa, para que "confianza" siga significando una sola cosa en todo
# el proyecto. Estos tres niveles miden que tan completa fue la LECTURA del
# archivo/carpeta de log, nunca si "actividad de log" equivale a "uso real"
# (esa es una brecha conceptual distinta, deliberadamente no modelada aqui).

# Se encontro y enumero POR COMPLETO una carpeta de logs (sin alcanzar el
# tope de MAX_LOG_ENTRIES_SCANNED/MAX_LOG_SCAN_SECONDS) -- la fecha
# reportada es el maximo real dentro de esa carpeta. Mismo nivel que
# REGEX_KEYWORD_MATCH: funciona bien, pero es reconocimiento por nombre de
# carpeta, no una verificacion.
FILE_LOG_FILE_MTIME_EXHAUSTIVE = 70

# Se encontro una carpeta de logs pero el escaneo alcanzo su tope (de
# entradas o de tiempo) antes de terminar -- la fecha reportada es el
# maximo encontrado HASTA ESE PUNTO, no necesariamente el archivo mas
# reciente real. Mismo nivel que INFERRED: sabemos algo, no todo.
FILE_LOG_FILE_MTIME_BOUNDED = 60

# Carpeta de logs reconocida por nombre, pero solo se leyo su propio mtime
# (barato, O(1), sin enumerar contenido) -- puede quedarse atras si la app
# escribe siempre al mismo archivo en vez de crear archivos nuevos dentro.
FILE_LOG_FOLDER_MTIME = 50


CONFIDENCE_TABLE: dict[str, int] = {
    "DB_INTROSPECT_DEFINITION": DB_INTROSPECT_DEFINITION,
    "DB_INTROSPECT_SCHEMA": DB_INTROSPECT_SCHEMA,
    "APP_CONFIG_EXPLICIT_CONNECTION": APP_CONFIG_EXPLICIT_CONNECTION,
    "SETTINGS_DEFAULT_VALUE": SETTINGS_DEFAULT_VALUE,
    "HARDCODED_METHOD_LITERAL": HARDCODED_METHOD_LITERAL,
    "SETTINGS_CLASS_LITERAL": SETTINGS_CLASS_LITERAL,
    "PARTIAL_RECONSTRUCTION": PARTIAL_RECONSTRUCTION,
    "REGEX_KEYWORD_MATCH": REGEX_KEYWORD_MATCH,
    "INFERRED": INFERRED,
    "DYNAMIC_SQL": DYNAMIC_SQL,
    "REFLECTION": REFLECTION,
    "FILE_LOG_FILE_MTIME_EXHAUSTIVE": FILE_LOG_FILE_MTIME_EXHAUSTIVE,
    "FILE_LOG_FILE_MTIME_BOUNDED": FILE_LOG_FILE_MTIME_BOUNDED,
    "FILE_LOG_FOLDER_MTIME": FILE_LOG_FOLDER_MTIME,
    "UNKNOWN": UNKNOWN,
}


def resolve_confidence(extractor_key: str) -> int:
    """Nunca lanza excepcion -- un extractor desconocido (typo, nombre nuevo
    todavia no catalogado) cae al piso de la escala (UNKNOWN=20) en vez de
    tumbar el analisis. Siempre regresa un entero valido en [0, 100]."""
    return CONFIDENCE_TABLE.get(extractor_key, UNKNOWN)
