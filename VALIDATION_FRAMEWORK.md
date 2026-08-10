# Validation Framework — Arquitectura Técnica

**Precondición**: este documento asume que `ARCHITECTURE_REVIEW.md` ya fue leído. Sus tres decisiones centrales condicionan todo lo que sigue y no se repiten en cada sección:

1. La confianza y la causa de no-resolución se capturan **en el momento de la extracción** (`extract.py`), nunca se reconstruyen después por inspección de lo ya guardado.
2. Confidence se **deriva** de qué función/patrón produjo el hallazgo (una tabla de mapeo centralizada), nunca se asigna a mano por hallazgo individual.
3. Las tablas nuevas (`unknowns`, `discovery_evidence`) se diseñan con una forma compatible con `findings`/`security_flags` (mismo vocabulario de severidad), reconociendo la deuda ya diagnosticada en `VISION.md` sección 11 sin intentar resolverla ahora.

**Terminología** (consistente con `VISION.md`): estos 8 componentes son la evolución interna de la **Capacidad #2 (Technical Analysis Engine)** más una nueva capacidad transversal de portafolio ("Coverage & Confidence"), no capacidades nuevas de primer nivel independientes. "Engine"/"Catalog" en los nombres de componente son etiquetas (igual que "UI Reconstruction Engine" en VISION.md), no una afirmación de que cada uno sea un motor intercambiable en el sentido de `ARCHITECTURE.md`.

---

## 0. La pieza central: `Evidence` como value object único

Todo lo demás en este documento depende de esta decisión, así que va primero, no al final (componente 6 originalmente, promovido).

### 0.1 Por qué un value object único y no 8 campos sueltos por tabla

`ARCHITECTURE_REVIEW.md` sección 2 documentó que cada dataclass (`SqlFinding`, `SettingEntry`, `LocalIOFinding`) ya se serializa a mano en 3 lugares. Agregar confidence+evidence+unknown-reason como campos sueltos a CADA una multiplica ese problema por 3. En vez de eso:

```python
# analyzer/evidence.py (nuevo módulo)

@dataclass(frozen=True)
class Evidence:
    file: str                      # ya existe hoy como `file` en SqlFinding/LocalIOFinding
    class_name: str                # ya existe
    method: str                    # ya existe
    line: int                      # NUEVO — hoy ningún finding guarda el numero de linea exacto
    extractor: str                 # NUEVO — nombre de la funcion/patron responsable, ej. "SQL_TRIGGER:new_SqlCommand"
    pattern: str                   # NUEVO — el patron regex/regla especifica que disparo (para poder decir "esto vino de la regla X", no solo "de extract.py")
    confidence: int                # NUEVO — 0-100, ver Confidence Engine (seccion 2)
    analyzer_version: str          # NUEVO — ver 0.3
    analyzed_at: str                # NUEVO — timestamp ISO, ya existe el patron en apps.analyzed_at
```

Un solo objeto, una sola forma. `SqlFinding`/`SettingEntry`/`LocalIOFinding` cada uno GANA un campo `evidence: Evidence` en vez de 5 campos sueltos cada uno. Esto no elimina el problema de los 3 puntos de serialización manual (sigue siendo trabajo real), pero lo reduce a **sincronizar UN tipo en 3 lugares**, no tres tipos distintos en 3 lugares cada uno — y es la base de la que se aprovechan directamente los componentes 6 y 7 (Discovery Evidence es, literalmente, esta misma estructura ya expuesta).

### 0.2 Dónde vive en la base de datos

No se crea una tabla `evidence` separada con FK hacia cada finding (eso sí sería sobre-ingeniería para lo que se necesita hoy). Se aplanan los campos de `Evidence` como columnas nuevas directamente en `sql_findings`/`settings`/`io_findings`, vía el mismo patrón de migración `ALTER TABLE` ya usado repetidamente en `db.py: init_db()` (ver ejemplo con `companion_assemblies`/`parameters`/`result_columns`):

```sql
ALTER TABLE sql_findings ADD COLUMN line INTEGER;
ALTER TABLE sql_findings ADD COLUMN extractor TEXT;
ALTER TABLE sql_findings ADD COLUMN pattern TEXT;
ALTER TABLE sql_findings ADD COLUMN confidence INTEGER;
ALTER TABLE sql_findings ADD COLUMN analyzer_version TEXT;
-- misma migracion para settings e io_findings
```

Justificación de aplanar en vez de normalizar: estos campos son 1:1 con cada finding (nunca N:1), así que una tabla separada solo agregaría un JOIN sin ganar nada — coherente con "evitar sobreingeniería" del proyecto.

### 0.3 Versión del analizador — prerequisito no resuelto hoy

Hoy no existe ningún `__version__` en el proyecto. Se introduce `analyzer/__version__.py` con una sola constante `ANALYZER_VERSION = "0.6.0"` (siguiendo semver, alineado a la numeración de VISION.md), importada por `evidence.py` para poblar `analyzer_version` en cada `Evidence` creado. Esto es prerequisito literal del componente 6 (Discovery Evidence pide "Versión del analizador" explícitamente) — sin esto no hay nada que poner en ese campo.

---

## 1. Discovery Coverage

**Pregunta que responde**: ¿qué porcentaje de los tipos de artefacto conocidos fueron encontrados (aunque sea parcialmente) en esta app?

### 1.1 Diseño: registro declarativo de tipos de artefacto, no una función nueva por tipo

Siguiendo la crítica de `ARCHITECTURE_REVIEW.md` sección 7 (evitar que cada métrica nueva sea una query SQL escrita a mano de forma aislada), se define un **registro declarativo** en `analyzer/coverage.py` (nuevo módulo):

```python
ARTIFACT_TYPES = {
    "connections":              lambda app_id: _count(settings, category="sql_or_oracle"),
    "stored_procedures":        lambda app_id: _count(sql_findings, category="stored_procedure"),
    "queries":                  lambda app_id: _count(sql_findings, category="query"),
    "tables":                   lambda app_id: _count_distinct_targets(sql_findings, category="query"),
    "views":                    ...,   # ver 1.3, hoy no distinguido de "tables"
    "functions":                ...,   # ver 1.3
    "reports":                  lambda app_id: _count(io_findings, operation__in=PRINT_REPORT_OPERATIONS),
    "business_rules":           lambda app_id: _count(findings, app_name=...),  # proxy ya documentado en Priority Engine
    "external_integrations":    lambda app_id: _count(io_findings, operation__in=INTEGRATION_OPERATIONS),
    "configuration_sources":    lambda app_id: _count(settings),
    "reflection":               lambda app_id: _count(reflection_findings),      # NUEVO tipo de finding, ver VALIDATION_STRATEGY Fase 4
    "dynamic_sql":              lambda app_id: _count(sql_findings, resolution_status="unresolved_dynamic_sql"),
    "file_access":              lambda app_id: _count(io_findings, operation__in=FILE_OPERATIONS),
    "hardware_integrations":    lambda app_id: _count(io_findings, operation__in=HARDWARE_OPERATIONS),  # SerialPort, Modbus (Fase 4)
}
```

Cada entrada es "¿cuántas filas EXISTEN de este tipo?" — Discovery Coverage nunca pregunta si están completas, solo si hay evidencia de su existencia. Esto es deliberadamente la métrica más fácil de calcular y la menos discutible: una fila en `sql_findings` con `category='stored_procedure'` cuenta como "SP descubierto" sin importar si después se pudo resolver su definición.

### 1.2 Por qué esto NO reemplaza los Read Models existentes

`get_table_dictionary()`/`get_dependency_graph()` siguen siendo los Read Models de negocio (para un arquitecto MES). Discovery Coverage es una capa distinta — un Read Model **sobre los Read Models**, cuya pregunta es "¿qué tan completo está el material que alimenta a los otros?", no "¿qué dice el material?". Se implementa como una función más en `db.py` (`get_discovery_coverage(app_id)`), mismo patrón arquitectónico que el Priority Engine, no un sistema separado.

### 1.3 Gap reconocido: Views y Functions no se distinguen hoy de Tables

`_classify_sql()` en `extract.py` no distingue entre una tabla real, una vista y una función con valores de tabla (TVF) — todas caen en `category='query'` con el nombre extraído del `FROM`. Esto es un gap real que este documento no resuelve (fuera de alcance de este framework — distinguirlas requeriría o bien introspección de BD por objeto, que ya existe parcialmente en `db_introspect.py` vía `sys.objects.type`, o heurísticas nuevas de C#). Se documenta explícitamente en `KNOWN_LIMITATIONS.md` en vez de fingir que el registro de arriba ya las separa — el campo `views`/`functions` del registro de 1.1 puede implementarse desde ahora consultando `db_procedures`/`db_tables` con `sys.objects.type IN ('V','TF','IF')` (ya lo trae `db_introspect.py` como columna disponible, no usada hoy), sin necesitar tocar el extractor de C#.

---

## 2. Resolution Coverage

**Pregunta que responde**: de lo que Discovery ya encontró, ¿qué porcentaje se resolvió COMPLETO?

### 2.1 El campo que falta hoy: `resolution_status`

Esta es la pieza de datos que `ARCHITECTURE_REVIEW.md` sección 3 identificó como ausente. Se agrega a `sql_findings` (y equivalente a `settings`):

```python
RESOLUTION_STATUSES = (
    "resolved",                    # texto/valor completo obtenido
    "unresolved_dynamic_sql",      # StringBuilder u otro ensamblado no lineal (Fase 3 de VALIDATION_STRATEGY)
    "unresolved_out_of_scope",     # variable resuelta fuera del metodo actual (campo de clase, Fase 2)
    "unresolved_no_literal",       # no hay ningun literal, causa no identificada especificamente (residual, ver 4.4)
    "not_applicable",              # ej. un `Trigger de I/O` no tiene "resolucion" en el mismo sentido que un SQL
)
```

Este campo se llena **en `extract.py`, en el momento del disparo** — no es un cálculo posterior. Cuando `_classify_sql()` (o su sucesor) no encuentra un literal resoluble, en vez de simplemente dejar `target=None` como hoy, retorna explícitamente CUÁL de las causas de `RESOLUTION_STATUSES` aplica, porque en ese momento el código todavía tiene el contexto para saberlo (vio un `StringBuilder`, o vio que la variable no estaba en el rango del método actual).

### 2.2 Fórmula de cobertura, sin heurísticas nuevas

```
Resolution Coverage (por tipo de artefacto) =
    COUNT(resolution_status = 'resolved') / COUNT(resolution_status != 'not_applicable')
```

Ejemplo textual del propio pedido del usuario, ahora con números reales derivables de la BD en vez de inventados:

```
Stored Procedures encontrados:      98   (Discovery)
  con definicion (db_procedures.status='ok'):     96
  con parametros (len(parameters_json) > 0):      94
  con tablas identificadas (join contra sql_findings de la misma SP): 93
Resolution Coverage: 93/98 = 94.9%
```

Nótese que esta cascada (definición → parámetros → tablas) ya es información que existe hoy en `db_procedures`/`sql_findings` — Resolution Coverage para SPs es, en gran parte, una **reorganización de datos ya presentes**, no una extracción nueva. El único campo verdaderamente nuevo es `resolution_status` para queries/settings (sección 2.1).

---

## 3. Confidence Engine

### 3.1 Regla de diseño (no negociable, pedida explícitamente por el usuario)

El confidence **nunca se asigna a mano por finding**. Se deriva mecánicamente de una tabla de mapeo `extractor -> confidence_base`, indexada por el mismo campo `evidence.extractor` de la sección 0:

```python
# analyzer/confidence.py (nuevo modulo, única fuente de verdad)

CONFIDENCE_TABLE = {
    # Introspeccion real contra el servidor -- maxima confianza posible, information
    # verificada contra la fuente de verdad misma.
    "db_introspect.get_procedure_definition":        100,
    "db_introspect.get_table_columns":               100,
    "db_introspect.INFORMATION_SCHEMA":              100,

    # Declarado explicitamente por el desarrollador, sin ambiguedad de parsing.
    "extract.find_appconfig_connection_strings":      98,
    "extract.find_settings.DefaultSettingValue":       95,

    # Literal de codigo, pero requiere que nuestro regex lo haya interpretado bien.
    "extract.resolve_variable.hardcoded_literal":      90,
    "extract.resolve_variable.class_field_literal":    88,   # nuevo, Fase 2 de VALIDATION_STRATEGY

    # Reconstruido parcialmente -- sabemos ALGO pero no todo (ej. target de un
    # StringBuilder donde solo se identifico una tabla de varias).
    "extract.classify_sql.partial_reconstruction":     80,

    # Heuristica de regex sobre texto ya resuelto -- funciona bien pero es
    # pattern-matching, no verificacion.
    "extract.classify_sql.regex_keyword_match":        70,

    # Inferencia sobre datos incompletos (ej. adivinar la tabla de un JOIN
    # parcialmente resuelto).
    "extract.infer_target.partial_context":            60,

    # Reflection detectado -- sabemos QUE existe reflection, pero por
    # definicion no podemos saber que va a invocar en tiempo de ejecucion.
    "extract.reflection_trigger":                      40,

    # No se pudo resolver nada -- el piso de la escala, nunca 0 (0 implicaria
    # "sabemos que es falso", cuando en realidad es "no sabemos").
    "extract.unresolved":                              20,
}
```

Cada entrada del catálogo lleva su propia justificación como comentario (pedido explícito del usuario: "cada regla deberá estar documentada") — el bloque de arriba ya lo hace inline, agrupado por categoría de método de obtención, replicando la tabla de ejemplo que el usuario mismo dio en su mensaje.

### 3.2 Cómo se usa

En el momento en que cualquier función de `extract.py`/`db_introspect.py`/`enrich.py` crea un `Evidence`, pasa el nombre de la función/patrón responsable; `Evidence.confidence` se resuelve una sola vez, en la construcción, vía `CONFIDENCE_TABLE.get(extractor_name, DEFAULT_LOW_CONFIDENCE)`. Ningún otro módulo (`report.py`, `db.py`, la futura UI) vuelve a calcular ni a interpretar confidence — solo lo muestran. Esto es intencional: **un solo lugar decide, todos los demás consumen**, mismo principio que `FACTOR_WEIGHTS` en el Priority Engine.

### 3.3 Nivel de app (agregado)

Confidence promedio por app = promedio simple de `confidence` sobre todos los findings de esa app con `resolution_status != 'not_applicable'`. Deliberadamente el promedio más simple posible (no ponderado, no Bayesiano) — coherente con "evitar heurísticas opacas"; si en el futuro se necesita ponderar (ej. una conexión pesa más que un archivo tocado), ese ajuste se documenta como un peso nuevo en una tabla central, igual que `FACTOR_WEIGHTS`, nunca como lógica dispersa.

---

## 4. Unknowns Engine

### 4.1 Modelo de datos

Nueva tabla `unknowns`, diseñada compatible con `findings` (mismo vocabulario de severidad ya usado, `FINDING_SEVERITIES`) para no repetir el problema ya diagnosticado en `ARCHITECTURE_REVIEW.md` sección 7:

```sql
CREATE TABLE IF NOT EXISTS unknowns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL,             -- keyed por nombre, igual que `findings`, sobrevive re-analisis
    category TEXT NOT NULL,             -- 'stored_procedure' | 'query' | 'connection' | 'reflection' | 'configuration' | ...
    reason_code TEXT NOT NULL,          -- FK logico hacia el Failure Reason Catalog (seccion 5), NUNCA texto libre
    impact TEXT NOT NULL,               -- frase corta, generada desde una plantilla del catalogo (no escrita a mano por finding)
    evidence_file TEXT,
    evidence_class TEXT,
    evidence_method TEXT,
    evidence_line INTEGER,
    suggested_action TEXT,              -- viene del catalogo (seccion 5), no se inventa aqui
    priority TEXT NOT NULL,             -- 'alta' | 'media' | 'baja' -- deriva de la severidad del reason_code, no manual
    created_at TEXT NOT NULL
);
```

### 4.2 Cómo se genera — nunca a mano

Un `Unknown` se crea automáticamente en el mismo punto de código donde algo queda con `resolution_status` distinto de `resolved` (sección 2.1) o donde `enrich.py` produce un `connection_error` (sección 5). **No existe una función "generar Unknowns" que corra aparte al final** — cada extractor, en el momento en que decide que algo no se resolvió, emite su propio `Unknown` con la causa que YA conoce en ese instante (mismo principio de la sección 0 de `ARCHITECTURE_REVIEW.md`).

### 4.3 Ejemplo concreto (el mismo que pidió el usuario, con los campos ya mapeados)

```
UNKNOWN
  Categoria:        stored_procedure
  reason_code:      SERVER_OFFLINE                (Failure Reason Catalog, seccion 5)
  Impacto:          "No fue posible recuperar definicion, parametros ni columnas de resultado"
                    (plantilla del catalogo para SERVER_OFFLINE + categoria=stored_procedure)
  Evidencia:        RLAltaConfig/RLAltaConfig.ViewModel/ConfVM.cs, clase ConfVM, metodo GetFCAll, linea 560
  Accion sugerida:  "Reintentar cuando NAAMRT-QCS11 vuelva a estar disponible, o actualizar la
                    connection string al servidor de reemplazo (NAAMRT-QCS25)"
                    (plantilla del catalogo para SERVER_OFFLINE, con el servidor interpolado)
  Prioridad:        alta (SERVER_OFFLINE se define como severidad alta en el catalogo)
```

### 4.4 El "cajón residual" — honestidad sobre sus propios límites

Habrá casos donde, incluso con todo lo anterior, el extractor no puede nombrar una causa más específica que `unresolved_no_literal` (sección 2.1). Esto NO se reporta como el genérico actual — se reporta como `reason_code = UNSUPPORTED_PATTERN` (sección 5), que es en sí mismo honesto ("encontramos algo que no reconocemos, no fingimos saber qué es") en vez de fingir precisión que no existe. La diferencia con el estado actual es que **hoy ni siquiera existe ese cajón como categoría explícita** — todo lo no resuelto es indistinguible.

---

## 5. Failure Reason Catalog

### 5.1 Diseño: enum cerrado + metadata, siguiendo el patrón ya usado en `db.py`

Mismo patrón arquitectónico que `FINDING_STATUSES`/`REVIEW_STATUSES` (tuplas cerradas ya en `db.py`), aplicado a causas de fallo:

```python
# analyzer/failure_catalog.py (nuevo modulo, unica fuente de verdad para TODA causa de fallo)

@dataclass(frozen=True)
class FailureReason:
    code: str
    technical_description: str     # para el desarrollador/auditor
    user_message_template: str     # para la UI -- puede tener placeholders, ej. "{server}"
    severity: str                  # reutiliza FINDING_SEVERITIES ("critica"|"alta"|"media"|"info")
    recommended_action: str

FAILURE_CATALOG: dict[str, FailureReason] = {
    "SERVER_OFFLINE": FailureReason(
        code="SERVER_OFFLINE",
        technical_description="El servidor esta en la lista de servidores confirmados como decomisionados/inalcanzables.",
        user_message_template="Servidor {server} no disponible (confirmado dado de baja) — no se intento conectar.",
        severity="alta",
        recommended_action="Actualizar la connection string al servidor de reemplazo conocido, o confirmar con infraestructura.",
    ),
    "DATABASE_NOT_FOUND": FailureReason(..., user_message_template="La base de datos {database} no existe en {server}.", severity="alta", ...),
    "LOGIN_FAILED": FailureReason(..., user_message_template="Autenticacion fallida contra {server} (usuario/password incorrecto o expirado).", severity="alta", ...),
    "TIMEOUT": FailureReason(..., user_message_template="{server} no respondio dentro del tiempo de espera (posible problema de red/VPN).", severity="media", ...),
    "DNS_NOT_RESOLVED": FailureReason(..., user_message_template="No se pudo resolver el nombre de host {server}.", severity="alta", ...),
    "PERMISSION_DENIED": FailureReason(..., user_message_template="Conexion exitosa a {server}, pero sin permiso para leer este objeto.", severity="media", ...),
    "DYNAMIC_SQL": FailureReason(..., user_message_template="SQL armado dinamicamente (StringBuilder/concatenacion multi-metodo) -- no se pudo determinar el texto completo.", severity="media", ...),
    "REFLECTION": FailureReason(..., user_message_template="El codigo invoca miembros via Reflection -- el comportamiento real depende de resolucion en tiempo de ejecucion.", severity="media", ...),
    "COM_OBJECT": FailureReason(..., user_message_template="Integracion via COM/ActiveX (ej. Excel) -- requiere el componente instalado en el host, no analizable estaticamente.", severity="media", ...),
    "UNSUPPORTED_PATTERN": FailureReason(..., user_message_template="Patron de codigo no reconocido por el extractor actual.", severity="info", ...),
    "UNKNOWN_CONFIGURATION_SOURCE": FailureReason(..., user_message_template="La conexion parece venir de una fuente de configuracion no soportada (Registry/INI/JSON/variable de entorno).", severity="info", ...),
    "VARIABLE_OUT_OF_METHOD_SCOPE": FailureReason(..., user_message_template="El valor se declara como campo de clase, fuera del alcance de busqueda del metodo actual.", severity="alta", ...),
    "SP_NOT_FOUND_OR_NO_PERMISSION": FailureReason(..., user_message_template="El objeto no existe con ese nombre, o el login no tiene permiso VIEW DEFINITION (SQL Server no distingue ambos casos).", severity="media", ...),
}
```

### 5.2 Quién lo consume

- `enrich.py`/`db_introspect.py`: mapean excepciones de pyodbc (SQLSTATE / mensaje) a un `code` de este catálogo, en vez de formatear texto libre (reemplaza `_short_error()`).
- `extract.py`: emite el `code` correspondiente en el momento en que decide un `resolution_status` no resuelto (sección 2.1) — el mapeo `resolution_status -> reason_code` es 1:1 y directo (`unresolved_dynamic_sql -> DYNAMIC_SQL`, etc.), no requiere lógica nueva.
- El Unknowns Engine (sección 4): cada `Unknown.reason_code` apunta a una entrada de este catálogo; `impact`/`suggested_action` en la tabla `unknowns` se generan interpolando la plantilla del catálogo, nunca se escriben a mano en el momento de crear el `Unknown`.

### 5.3 Mapeo de excepciones pyodbc → código (mecanismo, no lista exhaustiva)

pyodbc expone el SQLSTATE de ODBC en la excepción. Un mapeo simple y ya bien establecido en la industria (5 rangos de SQLSTATE cubren el 95% de los casos reales: `28000`=auth, `08001`/`08S01`=conexión/red, `HYT00`=timeout, `42000`=objeto no encontrado/permiso) es suficiente — no se necesita una librería nueva, es una función de `~15` líneas en `failure_catalog.py` (`code_from_sqlstate(sqlstate: str) -> str`), con un `UNSUPPORTED_PATTERN`-equivalente (`UNKNOWN_SQL_ERROR`) como default para SQLSTATEs no mapeados explícitamente — nunca se cae de vuelta al mensaje crudo de pyodbc como única salida.

---

## 6. Discovery Evidence

Ya diseñado en la sección 0 — este componente **es** el value object `Evidence` ya expuesto directamente en la UI/exportes, no una capa adicional. Lo único que agrega esta sección es la superficie de consulta:

```python
# db.py, nueva funcion, mismo patron Read Model que el resto
def get_evidence_for_finding(finding_type: str, finding_id: int) -> dict:
    """Retorna el Evidence completo (archivo, clase, metodo, linea, extractor,
    patron, confidence, version del analizador, fecha) para auditoria."""
```

Habilita exactamente la pregunta que pide el usuario ("¿qué funcionalidades del analizador participaron en cada descubrimiento?") vía el campo `extractor`, sin necesitar ninguna tabla ni estructura nueva más allá de las columnas ya agregadas en la sección 0.2.

---

## 7. Knowledge Graph Readiness (diseño, NO implementación)

### 7.1 Lo que ya existe y actúa como precursor real

`apps.companion_assemblies` + `analyzer/priority_report.py: _companion_assembly_observations()` **ya calculan aristas reales del grafo** (qué apps comparten qué ensamblado) — no es un diseño nuevo desde cero, es formalizar algo que ya funciona. Del mismo modo, `vw_dependency_graph`/`get_dependency_graph()` ya producen aristas App↔App vía tabla/SP compartido, y `get_pattern_catalog()` ya agrupa hallazgos por patrón recurrente cross-app.

### 7.2 Modelo de nodos y aristas propuesto (documentado, no construido)

```
Nodos:      Application, Connection, Server, Database, StoredProcedure,
            Table, Column, BusinessRule, Report, ExternalIntegration

Aristas:    Application --USES--> Connection
            Connection --POINTS_TO--> Server
            Connection --TARGETS--> Database
            Application --CALLS--> StoredProcedure
            StoredProcedure --READS/WRITES--> Table
            Table --HAS--> Column
            Application --IMPLEMENTS--> BusinessRule
            Application --SHARES_ASSEMBLY_WITH--> Application   (ya calculado hoy, ver 7.1)
            Application --SHARES_TABLE_WITH--> Application       (ya calculado hoy, ver 7.1)
            Application --SHARES_SERVER_WITH--> Application      (ya calculado hoy, ver 7.1)
```

### 7.3 Qué se prepara ahora vs. qué se implementa después

**Ahora** (parte de este framework, sin construir el grafo en sí): asegurar que cada nodo potencial tenga un identificador estable y consultable —
- `Application`: ya lo tiene (`apps.id`/`identity_id` futuro, ADR-0000).
- `Connection`/`Server`/`Database`: hoy son texto libre dentro de `settings.default_value`; se recomienda que `db_introspect.parse_dotnet_connection_string()` (ya existe, usado hoy solo para el Dependency Graph) sea la función canónica que produce estos 3 "nodos" en forma normalizada, reutilizada por Evidence/Coverage en vez de que cada consumidor futuro reimplemente su propio parseo.
- `StoredProcedure`/`Table`/`Column`: ya identificables por `schema.nombre` en `db_procedures`/`db_tables`.
- `BusinessRule`: sigue siendo prosa libre en `review_notes` (deuda ya conocida desde `AUDIT-ARB-2026-08-04`, sin resolver aquí — fuera de alcance).

**Después** (explícitamente NO en este framework): la tabla de nodos/aristas real, cualquier motor de consulta de grafo, cualquier visualización. Se documenta la forma para que cuando llegue el momento (v2.0 en el roadmap de VISION.md, "Knowledge Engine proactivo") no haya que re-descubrir qué relaciones ya existían de forma dispersa.

---

## 8. Regression Framework (diseño — ver `TEST_STRATEGY.md` para el detalle operativo)

### 8.1 Principio

Cada bug corregido en `extract.py`/`enrich.py`/`db_introspect.py` de aquí en adelante **debe** convertirse en un fixture permanente antes de cerrarse como resuelto — no es una sugerencia de proceso, es una regla de aceptación (ver `IMPLEMENTATION_PLAN.md` para cómo se aplica en la práctica).

### 8.2 Por qué fixtures reales del portafolio, no sintéticos

Los 6 casos que el usuario menciona explícitamente (`ReportViewer`, `InterConfig`, `InterAFL`, `SGI`, `DataTransfer`, `AlmacenDiagnostico`) ya son código C# real, ya decompilado, con un veredicto humano ya verificado (se sabe exactamente qué debería encontrar el extractor en cada uno). Escribir C# sintético de prueba sería trabajo adicional Y menos confiable que reutilizar evidencia ya validada — se usan directamente los archivos bajo `decompiled/` como fixtures, congelados (copiados a `tests/fixtures/`, no referenciando la carpeta `decompiled/` en vivo, que puede regenerarse/borrarse).

### 8.3 Mecanismo: snapshot dorado (golden output) por fixture

Para cada fixture, un archivo JSON pequeño con el resultado esperado (no todo el output, solo los campos que importan para ese bug específico) — ej. para `SGI/SurtirVM.cs`: `{"target": "ValeRH", "resolution_status": "resolved"}`, no una comparación byte-a-byte de todo `sql_findings`. Esto evita que el test se vuelva frágil ante cambios no relacionados (ej. agregar un campo nuevo a `Evidence` no debería romper 6 tests viejos que no lo mencionan).

---

## 9. Riesgos de este propio diseño (autocrítica)

- **El campo `resolution_status` requiere tocar `_classify_sql()`, que ya es la función más densa y más veces corregida del proyecto** (ver historial de bugs de la memoria del proyecto: classification bugs corregidos al menos 2 veces antes). Es exactamente donde más se necesita el Regression Framework de la sección 8 funcionando ANTES de tocarlo, no después.
- **El promedio simple de confidence a nivel app (sección 3.3) puede ser engañoso** si una app tiene muchos findings triviales de alta confianza (ej. 50 `File.Exists` bien resueltos) y pocos findings críticos de baja confianza (ej. 2 SPs sin resolver) — el promedio se vería alto pese a un vacío real importante. Se documenta esto como limitación conocida desde el diseño (no descubierta después) — si se vuelve un problema real, la mitigación es ponderar por severidad/categoría, no cambiar la fórmula base sin evidencia de que hace falta (Principio 4, "la complejidad solo crece con evidencia objetiva").
- **El Failure Reason Catalog de la sección 5 es, en sí mismo, una heurística de mapeo SQLSTATE→causa** — no es infalible (dos SQLSTATEs distintos de proveedores/versiones de driver distintas pueden mapear a la misma causa real, o el mismo SQLSTATE puede significar cosas distintas en Oracle vs SQL Server). Se documenta el residual (`UNKNOWN_SQL_ERROR`) explícitamente en vez de fingir cobertura 100% del espacio de errores posibles.
