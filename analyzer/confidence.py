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


# Incremento Huella de Datos (2026-08-18, analyzer/server_resolution.py):
# a que SERVIDOR/BD pertenece una escritura SQL ya encontrada -- una
# dimension distinta de "que tan seguro estoy del contenido del SQL"
# (las de arriba). Reutiliza la misma escala 0-100, nunca una paralela.

# El argumento del constructor (new SqlConnection(X)) o una asignacion
# posterior a .ConnectionString se traza directamente a un setting conocido,
# DENTRO DEL MISMO METODO -- misma certeza que leer el valor del setting en
# si (SETTINGS_DEFAULT_VALUE), solo que la forma del codigo es distinta
# (ctor-arg o property-assignment en vez de una asignacion de variable).
CONNECTION_CTOR_DIRECT_SETTING = 95

# Dos o mas conexiones nombradas son candidatas validas segun el codigo
# (tipicamente un operador ternario `cond ? CX2 : CX`) y cual se usa
# realmente depende de una condicion que no se evalua (fuera de alcance:
# nada de ejecucion simbolica). Se sabe que es una de un conjunto pequeno y
# conocido -- mas cierto que SQL dinamico, pero deliberadamente NO resuelto.
CONNECTION_AMBIGUOUS_CONDITIONAL = 55

# Incremento Mapa de Flujo de Datos (2026-08-19, analyzer/data_flow.py): de
# donde sale la lista de columnas de un INSERT/UPDATE/SELECT -- dimension
# distinta de "que tan seguro estoy de la conexion" (las de arriba).

# La lista de columnas viene de SqlFinding.result_columns, ya poblado por
# extract.py a partir de accesos reales reader["X"] en el codigo -- la
# fuente mas precisa posible, refleja lo que el codigo REALMENTE lee, no
# solo lo que la query tecnicamente devuelve.
DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS = 90

# La lista de columnas se reconstruyo por regex sobre un texto SQL ya
# completamente literal (resolved o raw) -- un paso mas de procesamiento
# lejos de la fuente que result_columns, pero confirmado confiable contra
# 9 ejemplos reales del portafolio (INSERT/UPDATE/SELECT con lista de
# columnas 100% literal incluso cuando el WHERE tiene concatenacion).
DATA_ROLE_COLUMNS_FROM_LITERAL_SQL = 80

# Incremento Flujo de Aplicacion - Application Structure Discovery
# (2026-08-20, analyzer/app_structure.py): confianza de la evidencia
# ESTRUCTURAL de una app (entry point / clase / metodo), dimension
# independiente de "que tan seguro estoy del SQL/conexion" (las de arriba).

# Application.Run(...) es la senal mas explicita e inequivoca de arranque
# WinForms -- confirmada real en 49 archivos del portafolio (Andon,
# CopyJDSU, etc.), incluso envuelta en un guard de instancia unica.
APP_STRUCTURE_ENTRY_POINT_APPLICATION_RUN = 95

# Patron WPF real confirmado (decompiled/AFL.Entrega/.../App.cs): se crea
# una instancia de la clase "App" y luego se llama a su .Run() -- un paso
# mas de inferencia que Application.Run() directo (se depende de la
# convencion de nombre "App"), pero el patron completo (instancia + .Run()
# sobre esa MISMA variable) es evidencia real, no una suposicion.
APP_STRUCTURE_ENTRY_POINT_WPF_APP_RUN = 90

# Un metodo llamado "Main" es un entry point real por convencion de C#,
# pero sin Application.Run()/App().Run() visible no hay evidencia de QUE
# arranca -- confianza mas baja que los dos patrones de arriba.
APP_STRUCTURE_ENTRY_POINT_BARE_MAIN = 70

# Declaracion de clase (nombre + tipo base si existe) via coincidencia
# directa de regex sobre el codigo ya decompilado -- evidencia sintactica
# directa, sin inferencia.
APP_STRUCTURE_CLASS_DECLARATION = 90

# Declaracion de metodo (via el mismo patron ya validado en
# analyzer/extract.py::METHOD_SIG contra el portafolio real completo),
# acotada a su clase contenedora por brace-matching -- mismo nivel de
# certeza sintactica que la declaracion de clase.
APP_STRUCTURE_METHOD_DECLARATION = 85

# Incremento Flujo de Aplicacion - Navigation Discovery (2026-08-20,
# analyzer/app_navigation.py): confianza de una relacion de NAVEGACION
# entre Forms/Windows (instancia + .Show()/.ShowDialog() dentro del mismo
# metodo) -- dimension independiente de la confianza estructural de arriba
# (esa mide "existe esta clase/metodo", esta mide "esta relacion de
# navegacion entre dos pantallas es real").

# Instanciacion ("= new TypeName(") y .Show()/.ShowDialog() sobre la MISMA
# variable, dentro del MISMO metodo, y el tipo resuelto SI fue clasificado
# como Form/Window por Application Structure Discovery -- evidencia
# sintactica directa y completa, sin ningun eslabon sin confirmar.
APP_NAVIGATION_INSTANTIATION_AND_SHOW = 90

# Se observo .Show()/.ShowDialog() real sobre una variable, pero el tipo de
# esa variable no se pudo determinar dentro del mismo metodo (ej. proviene
# de una factory/metodo en vez de "new TypeName(") -- se sabe QUE algo se
# muestra, no QUE se muestra. Deliberadamente bajo: no alcanza para
# "target_type_unknown" bajo INFERRED.
APP_NAVIGATION_TARGET_TYPE_UNKNOWN = 30

# El tipo mostrado SI se resolvio por nombre (instanciacion + Show/
# ShowDialog en el mismo metodo), pero esa clase no fue confirmada como
# Form/Window por Application Structure Discovery -- mas evidencia que el
# caso anterior (se conoce el nombre del tipo), pero no se afirma que sea
# una pantalla real sin esa confirmacion independiente.
APP_NAVIGATION_TARGET_NOT_CONFIRMED_SCREEN = 50

# Incremento Flujo de Aplicacion - Event Wiring + Intra-Class Call Flow
# (2026-08-20, analyzer/app_interactions.py): confianza de una asociacion
# control->handler o de una llamada metodo->metodo -- dimensiones
# independientes de la confianza de Navigation (arriba).

# "control.Evento += new Tipo(handler);" o "control.Evento += handler;"
# donde "handler" es un metodo REAL ya confirmado en la misma clase --
# evidencia sintactica directa y completa, sin ningun eslabon sin
# confirmar (misma certeza que una declaracion de clase/metodo).
APP_EVENT_WIRING_EXPLICIT = 95

# Se observo "control.Evento += <algo con forma de llamada o lambda>", pero
# el handler concreto no se pudo determinar con seguridad (ej. wrapper
# indirecto, lambda) -- se sabe QUE hay wiring, no QUE metodo maneja el
# evento. Mismo nivel que DYNAMIC_SQL/REFLECTION: sabemos que la relacion
# existe, no su contenido exacto.
APP_EVENT_WIRING_HANDLER_UNKNOWN = 40

# Llamada sin receptor (o receptor "this") a un nombre que coincide con
# EXACTAMENTE un metodo de la misma clase -- evidencia sintactica directa.
APP_CALL_INTRA_CLASS = 90

# El nombre coincide con 2+ metodos de la misma clase (sobrecarga real,
# calculada POR ARCHIVO para no confundir sobrecarga con duplicacion fisica
# de codigo) -- deliberadamente no se cuenta argumentos para desambiguar
# (fuera de alcance: "no overload resolution complejo").
APP_CALL_TARGET_AMBIGUOUS = 40

# El nombre no coincide con ningun metodo de la clase actual, pero SI con
# un metodo de la clase base declarada de esta clase (un solo salto, sin
# resolucion de herencia/MRO completa) -- mas evidencia que "desconocido"
# porque se confirma que el nombre existe en algun lugar del arbol de tipos
# conocido, pero no se afirma resolucion definitiva sin modelar herencia.
APP_CALL_TARGET_INHERITED = 50

# El nombre no coincide con ningun metodo de la clase actual ni de su clase
# base directa -- puede ser BCL, un campo delegado invocado directamente,
# una local function (fuera de alcance), o simplemente no existir. Piso de
# la escala, igual que UNKNOWN: "no sabemos", nunca "no existe".
APP_CALL_TARGET_UNKNOWN = 30

# Incremento Flujo de Aplicacion - Data Flow Integration (2026-08-20,
# analyzer/app_data_flow.py): confianza de la ATRIBUCION Method->SqlFinding
# -- dimension independiente de la confianza del propio contenido SQL (ya
# cubierta por DATA_ROLE_COLUMNS_*/REGEX_KEYWORD_MATCH/etc., reutilizada tal
# cual via DataFlowEdge.role, nunca recalculada aqui).

# (class_name, method) coincide EXACTAMENTE con un MethodInfo de
# Application Structure Discovery, confirmado ademas por archivo (mismo
# nivel de certeza sintactica que APP_CALL_INTRA_CLASS: atribucion directa
# y completa, sin ningun eslabon sin confirmar).
APP_DATA_METHOD_SQL_DIRECT = 90

# El SqlFinding no se pudo atribuir a un MethodInfo con seguridad (0
# candidatos tras filtrar por archivo, o 2+ candidatos con firma distinta
# -- sobrecarga real, nunca elegida arbitrariamente). Mismo nivel que
# APP_CALL_TARGET_AMBIGUOUS: sabemos que el SqlFinding existe, no a que
# metodo especifico pertenece con certeza.
APP_DATA_METHOD_SQL_MAPPING_AMBIGUOUS = 30

# Operacion propagada UN SOLO salto via Call Flow intra-class (A llama a
# B, B ejecuta el SQL) -- deliberadamente menor que la atribucion directa:
# la operacion en si (tabla/rol) esta tan confirmada como en el caso
# directo, pero la ATRIBUCION al metodo llamante es un eslabon adicional de
# inferencia (depende de que el CallEdge intermedio tambien este resuelto).
APP_DATA_INDIRECT_VIA_CALL_FLOW = 70

# Incremento Flujo de Aplicacion - Screen Surface Discovery (2026-08-21,
# analyzer/app_controls.py): confianza de la identidad/tipo/texto de un
# control WinForms -- dimension independiente de la confianza estructural
# de Application Structure Discovery (esa mide "existe esta clase/metodo",
# esta mide "esta pantalla tiene este control, de este tipo, con este
# texto").

# Tipo confirmado por declaracion de campo bare ("private Tipo nombre;",
# tipo del catalogo curado) y/o instanciacion completamente calificada
# ("this.nombre = new System.Windows.Forms.Tipo(...)") -- evidencia
# sintactica directa, sin inferencia de nombre de variable.
APP_CONTROL_DECLARATION_AND_TYPE = 90

# Igual certeza que el tipo (evidencia sintactica directa: asignacion
# literal "this.nombre.Text = "algo";"), documentado con su propia clave
# para poder distinguir en reportes futuros los controles con texto
# confirmado de los que solo tienen tipo confirmado -- NUNCA implica que
# la ausencia de texto sea menos confiable (ver Caso A: control conocido,
# texto no observado, jamas "unknown").
APP_CONTROL_LABEL_TEXT = 90

# Se observo instanciacion "this.nombre = new System.Windows.Forms.Tipo(...)"
# real, pero "Tipo" no pertenece al catalogo curado -- sabemos QUE existe un
# control, no QUE tipo concreto es. Piso de la escala, igual que UNKNOWN:
# "no sabemos", nunca "no existe" ni "no es un control valido".
APP_CONTROL_TYPE_UNKNOWN = 30


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
    "CONNECTION_CTOR_DIRECT_SETTING": CONNECTION_CTOR_DIRECT_SETTING,
    "CONNECTION_AMBIGUOUS_CONDITIONAL": CONNECTION_AMBIGUOUS_CONDITIONAL,
    "DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS": DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS,
    "DATA_ROLE_COLUMNS_FROM_LITERAL_SQL": DATA_ROLE_COLUMNS_FROM_LITERAL_SQL,
    "APP_STRUCTURE_ENTRY_POINT_APPLICATION_RUN": APP_STRUCTURE_ENTRY_POINT_APPLICATION_RUN,
    "APP_STRUCTURE_ENTRY_POINT_WPF_APP_RUN": APP_STRUCTURE_ENTRY_POINT_WPF_APP_RUN,
    "APP_STRUCTURE_ENTRY_POINT_BARE_MAIN": APP_STRUCTURE_ENTRY_POINT_BARE_MAIN,
    "APP_STRUCTURE_CLASS_DECLARATION": APP_STRUCTURE_CLASS_DECLARATION,
    "APP_STRUCTURE_METHOD_DECLARATION": APP_STRUCTURE_METHOD_DECLARATION,
    "APP_NAVIGATION_INSTANTIATION_AND_SHOW": APP_NAVIGATION_INSTANTIATION_AND_SHOW,
    "APP_NAVIGATION_TARGET_TYPE_UNKNOWN": APP_NAVIGATION_TARGET_TYPE_UNKNOWN,
    "APP_NAVIGATION_TARGET_NOT_CONFIRMED_SCREEN": APP_NAVIGATION_TARGET_NOT_CONFIRMED_SCREEN,
    "APP_EVENT_WIRING_EXPLICIT": APP_EVENT_WIRING_EXPLICIT,
    "APP_EVENT_WIRING_HANDLER_UNKNOWN": APP_EVENT_WIRING_HANDLER_UNKNOWN,
    "APP_CALL_INTRA_CLASS": APP_CALL_INTRA_CLASS,
    "APP_CALL_TARGET_AMBIGUOUS": APP_CALL_TARGET_AMBIGUOUS,
    "APP_CALL_TARGET_INHERITED": APP_CALL_TARGET_INHERITED,
    "APP_CALL_TARGET_UNKNOWN": APP_CALL_TARGET_UNKNOWN,
    "APP_DATA_METHOD_SQL_DIRECT": APP_DATA_METHOD_SQL_DIRECT,
    "APP_DATA_METHOD_SQL_MAPPING_AMBIGUOUS": APP_DATA_METHOD_SQL_MAPPING_AMBIGUOUS,
    "APP_DATA_INDIRECT_VIA_CALL_FLOW": APP_DATA_INDIRECT_VIA_CALL_FLOW,
    "APP_CONTROL_DECLARATION_AND_TYPE": APP_CONTROL_DECLARATION_AND_TYPE,
    "APP_CONTROL_LABEL_TEXT": APP_CONTROL_LABEL_TEXT,
    "APP_CONTROL_TYPE_UNKNOWN": APP_CONTROL_TYPE_UNKNOWN,
    "UNKNOWN": UNKNOWN,
}


def resolve_confidence(extractor_key: str) -> int:
    """Nunca lanza excepcion -- un extractor desconocido (typo, nombre nuevo
    todavia no catalogado) cae al piso de la escala (UNKNOWN=20) en vez de
    tumbar el analisis. Siempre regresa un entero valido en [0, 100]."""
    return CONFIDENCE_TABLE.get(extractor_key, UNKNOWN)
