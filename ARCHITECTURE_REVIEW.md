# Revisión Crítica de Arquitectura — pre-Validation Framework

**Rol asumido**: Principal Engineer / Software Architect revisando si la arquitectura actual soporta, sin retrabajo mayor, las 8 capacidades pedidas en el Validation Framework (Discovery/Resolution Coverage, Confidence Engine, Unknowns Engine, Failure Reason Catalog, Discovery Evidence, Knowledge Graph Readiness, Regression Framework). La pregunta que este documento responde no es "¿funciona hoy?" — ya sabemos que sí, para lo que cubre — sino **"¿el diseño actual se rompe, se duplica o se vuelve inconsistente en cuanto le agreguemos estas 8 capacidades encima?"**

**Regla seguida**: no se asume que ninguna decisión actual es correcta solo porque ya funciona. Cada sección identifica el problema con evidencia (archivo:línea), no con generalidades.

---

## 1. El hallazgo central: confianza y evidencia no tienen dónde vivir hoy

Ninguna de las tres dataclasses que representan lo que la herramienta extrae (`SqlFinding`, `SettingEntry`, `LocalIOFinding` en `analyzer/extract.py`) tiene un solo campo relacionado con **cómo se obtuvo el dato** ni **qué tan seguro es**. Comparando contra lo que el Validation Framework necesita:

| Necesita el framework | Existe hoy |
|---|---|
| Confidence Score por hallazgo | ❌ Ningún campo |
| Evidencia (extractor responsable, patrón usado, versión del analizador, fecha) | ❌ Ningún campo — solo `file`/`class_name`/`method`, sin línea exacta, sin patrón, sin versión |
| Motivo de no-resolución (causa estructurada) | ❌ Ningún campo — cuando algo no se resuelve, la información se **descarta**, no se registra |

Esto no es "falta un campo", es una consecuencia directa de una decisión de diseño implícita: **el extractor fue diseñado para producir "¿qué encontramos?", nunca para producir "¿qué tan seguros estamos y por qué no llegamos más lejos?"**. Añadir eso ahora no es agregar una columna — es agregar una dimensión completa a cada punto de extracción del código.

### 1.1 Consecuencia concreta ya observada: la causa se pierde en el momento en que ocurre, no se puede reconstruir después

`analyzer/report.py:48-52` fabrica el mensaje genérico `"(conexion detectada, query no resuelta automaticamente — revisar manualmente)"` cuando un grupo de hallazgos no tiene ningún literal con comillas. Pero para cuando `report.py` corre, **ya es demasiado tarde para saber por qué** — `extract.py: scan_file()` (línea 373-417) ya decidió no resolver la variable (por ejemplo, un `StringBuilder` o un campo de clase, ver `VALIDATION_STRATEGY.md` sección 2.B) y simplemente dejó `resolved=None`, sin dejar ningún rastro de *cuál* de los varios motivos posibles fue.

**Esto es la prueba de que "Unknowns" y "Failure Reason Catalog" no se pueden implementar como una capa de presentación sobre los datos actuales.** Tienen que capturarse en el momento exacto de la extracción (`extract.py`), donde la información sobre la causa todavía existe, no reconstruirse adivinando en `report.py` o en un motor de coverage posterior. Cualquier diseño que intente calcular "Unknowns" analizando los `sql_findings` ya guardados (post-hoc) estará adivinando la causa en vez de conociéndola — exactamente el patrón "mensaje genérico" que se quiere eliminar, solo que un nivel más abajo.

**Decisión que este documento fuerza**: el Failure Reason Catalog y el campo de confianza deben ser parte de la firma de retorno de las funciones de `extract.py`, no calculados después. Ver `VALIDATION_FRAMEWORK.md` sección 3-4 para el diseño concreto.

---

## 2. Acoplamiento por duplicación: el mismo dataclass se serializa a mano en 4 lugares distintos

Rastreando `SqlFinding` (el caso más grave, aplica igual a `SettingEntry`/`LocalIOFinding`) a través del código:

1. **Definición + población**: `analyzer/extract.py` (`@dataclass class SqlFinding`, poblado en `scan_file()`).
2. **Persistencia**: `analyzer/db.py: save_analysis()` (líneas ~244-256) — INSERT manual columna por columna, incluyendo `json.dumps(f.parameters)`/`json.dumps(f.result_columns)` para los dos campos tipo lista.
3. **Reconstrucción desde la BD**: `analyzer/report.py: reconstruct_from_db()` (líneas 272-281) — vuelve a construir el mismo dataclass a mano, columna por columna, incluyendo `json.loads(...)` simétrico para los mismos dos campos.
4. **Exportación**: `analyzer/export_office.py` — reutiliza el resultado de `reconstruct_from_db`, así que al menos no triplica la lógica, pero SÍ implica que cualquier campo nuevo tiene que fluir correctamente por los pasos 1→2→3 antes de que `export_office.py` pueda usarlo.

**El problema no es que esto no funcione — funciona, y lo ha hecho bien durante toda la vida del proyecto.** El problema es que **agregar los campos que pide el Validation Framework (confidence, evidence, unknown_reason) a `SqlFinding` obliga a tocar los 3 puntos de serialización manual de forma perfectamente sincronizada**, y no existe ningún mecanismo (ni un test, ver sección 5) que detecte si uno de los tres se queda desactualizado. Ya hay precedente de esto saliendo mal: el historial del proyecto registra al menos un bug real ("stored-procedure name detection... corrió contra el string C# completo en vez del literal") causado por exactamente este tipo de desincronización entre capas.

**Decisión que este documento fuerza**: antes de agregar 3 campos nuevos a 3 dataclasses que ya se serializan a mano en 3 lugares cada una (9 puntos de cambio coordinado), conviene introducir un único punto de conversión dataclass↔fila-de-BD (ver sección 6, refactor propuesto), no seguir copiando el patrón de "columna por columna, a mano, en cada capa".

---

## 3. Ambigüedad ya existente entre "no encontramos nada" y "encontramos algo pero no lo resolvimos"

`sql_findings.target IS NULL` hoy significa **dos cosas completamente distintas** sin ninguna forma de distinguirlas desde el esquema:
- Caso A: la query fue resuelta correctamente y genuinamente no tiene una tabla identificable (ej. un `SELECT 1` o un procedimiento sin FROM/INTO/UPDATE).
- Caso B: la query fue armada con `StringBuilder` (o una variable de campo de clase) y el extractor nunca pudo ver el texto real — no es que "no tenga tabla", es que **no sabemos si la tiene**.

Todo lo que consume `sql_findings.target` hoy (`db.get_dependency_graph()`, `db.get_table_dictionary()`, el Priority & Complexity Engine vía `_factor_complejidad_tecnica`) trata ambos casos IGUAL — como "sin destino conocido". Esto significa que **el Grafo de Dependencias y el Diccionario de Datos del portafolio ya subestiman silenciosamente** las apps con SQL dinámico (confirmado: `DataTransfer` y `SGI`, las 2 apps más grandes del portafolio, tienen queries reales sobre `ValeRH`/`ValePartes`/`XXAFL_QAPV_REWORKS_PRUEBA` invisibles a esos dos Read Models).

Esta ambigüedad es exactamente la distinción que el framework pide entre **Discovery Coverage** ("¿encontramos ALGO?" — el caso B sí cuenta como encontrado, hay una fila) y **Resolution Coverage** ("¿lo resolvimos COMPLETO?" — el caso B falla aquí). El esquema actual no tiene ningún campo que represente esta distinción — se necesita un estado explícito (`resolution_status`: `resolved` / `unresolved_dynamic_sql` / `unresolved_out_of_scope` / etc.), no inferirlo de si `target` es NULL o no.

---

## 4. `enrich.py`: los errores de conexión son texto libre, no datos estructurados

`connection_errors: list[str]` (tipo de retorno de `enrich_app()`, `analyzer/enrich.py`) es una lista de strings ya formateados para mostrarse — la información estructurada (¿fue timeout? ¿fue DNS? ¿fue login? ¿qué servidor?) se pierde en el momento en que `_short_error()` (línea 37-44) colapsa la excepción de pyodbc en una de dos frases. El **Failure Reason Catalog** (componente 5 del framework) no puede construirse sobre esto sin antes cambiar `_short_error()` para que retorne un **código de causa estructurado** (`SERVER_OFFLINE`/`TIMEOUT`/`LOGIN_FAILED`/etc.) del cual el mensaje de texto sea una PROYECCIÓN, no la fuente de verdad. Hoy es al revés: el texto es la única representación que existe.

Nótese que ya existe un ejemplo correcto de esto en el propio `enrich.py`: `KNOWN_UNREACHABLE_SERVERS` (línea 25) es, de hecho, el único caso hoy que se comporta como debería comportarse TODO el catálogo de causas — una causa con nombre (`servidor decomisionado`), no una excepción cruda reformateada. El Failure Reason Catalog es, en esencia, generalizar ese patrón (que ya demostró funcionar bien) a todas las demás causas posibles, no inventar un mecanismo nuevo.

---

## 5. Ausencia total de red de seguridad de regresión

Ya documentado en `VALIDATION_STRATEGY.md` sección 8, se repite aquí porque es una precondición arquitectónica, no un detalle de proceso: **no existe `pytest` en `requirements.txt`, no existe carpeta `tests/`, no existe `conftest.py`.** Esto es especialmente grave en el contexto de este framework específico, porque:
- Cualquier cambio a `extract.py`/`db.py`/`enrich.py` para agregar confidence/evidence/unknown-reason toca código que ya tiene ~15 bugs históricos conocidos y corregidos (ver memoria del proyecto) — sin tests, cada fix nuevo puede reintroducir silenciosamente uno de esos 15.
- El propio framework (Discovery/Resolution Coverage) **es**, en esencia, un sistema de métricas — construir un sistema de métricas sin tests que verifiquen que el cálculo es correcto es, literalmente, construir la herramienta que se supone iba a dar confianza objetiva sin tener ninguna confianza objetiva en la herramienta misma.

**Esto no es negociable como parte del plan de implementación** (ver `IMPLEMENTATION_PLAN.md`): la Fase 0 tiene que ser introducir la infraestructura de test, antes de tocar `extract.py`.

---

## 6. Responsabilidades mezcladas identificadas (por módulo)

| Módulo | Responsabilidad que declara tener | Responsabilidad que en realidad también tiene (mezclada) |
|---|---|---|
| `analyzer/report.py` | "Renderiza un análisis ya hecho a Markdown" | **También decide qué significa "no resuelto"** (línea 48-52) — una decisión de negocio/diagnóstico, no de presentación. Debería solo formatear una causa que ya le llega estructurada. |
| `analyzer/enrich.py` | "Orquesta introspección de solo lectura" | **También formatea mensajes de error para el usuario final** (`_short_error`) — mezcla "decidir la causa técnica" con "decidir cómo se ve el texto en pantalla". |
| `analyzer/db.py` | "Persistencia + Read Models de portafolio" | Ya es grande (810+ líneas) y sigue creciendo con cada capacidad nueva (Priority Engine, ahora potencialmente Coverage/Confidence) — `ARCHITECTURE.md` ya señaló este riesgo en la autoevaluación de v0.5 ("a la escala de 17 capacidades... ese archivo puede convertirse en algo de miles de líneas difícil de navegar") sin haber decidido todavía la convención futura. **Este framework es el momento de decidirlo**, no siete capacidades más tarde. |
| `analyzer/extract.py` | "Extrae SQL/settings/IO del código fuente" | Correcta y enfocada — el problema no es que haga de más, es que **no tiene ningún lugar para expresar incertidumbre** (sección 1). No se está mezclando responsabilidades aquí, está incompleta para lo que se le va a pedir. |

---

## 7. Duplicación conceptual entre tablas ya existentes — antes de crear más

El proyecto ya tiene, en `db.py`, **tres tablas distintas que representan "algo que se observó sobre una app"**, cada una con su propia forma:

1. `security_flags` (severity, description, location) — generado automáticamente, sin lifecycle.
2. `findings` (severity, title, description, status con lifecycle OPEN/ACKNOWLEDGED/RESOLVED/FALSE_POSITIVE/IGNORED, keyed por `app_name` no `app_id` a propósito) — curado manualmente.
3. `sql_findings`/`io_findings` (categoría, target, parámetros...) — estructurados, específicos de SQL/IO.

`VISION.md` sección 11 **ya diagnosticó este problema** en agosto 2026 ("`FINDING` sigue siendo una sola entidad sin discriminador de tipo... Si Testing Engine y Migration Readiness Checklist empiezan a depender de FINDING antes de resolver esto, arreglarlo después significa tocar todo lo que ya se construyó encima") y lo dejó como decisión #5 pendiente, sin bloquear v0.5.

**Este framework agrega una CUARTA tabla de la misma familia conceptual: `unknowns` (componente 4), y potencialmente una QUINTA: `discovery_evidence` (componente 6).** Ignorar la advertencia de VISION.md una vez más — construir `unknowns` con su propia forma aislada, sin relación con `findings`/`security_flags` — es exactamente el escenario que esa sección ya predijo como costoso de corregir después.

**Decisión que este documento fuerza**: no se propone (todavía) unificar las 3 tablas existentes — sería una migración grande, fuera de alcance de este framework y no pedida por el usuario. Pero **sí se propone que las 2 tablas nuevas (`unknowns`, `discovery_evidence`) se diseñen desde el inicio con un discriminador de tipo y una forma compatible con una futura unificación**, en vez de repetir el error una tercera vez. Ver `VALIDATION_FRAMEWORK.md` sección 6 para el modelo de datos propuesto — usa exactamente el mismo vocabulario de severidad/categoría que `findings`/`security_flags` ya usan, no uno nuevo.

---

## 8. Lo que SÍ está bien y no debe tocarse

Para que la revisión sea honesta y no "cuestionar por cuestionar":

- **El invariante de solo-lectura de `db_introspect.py`** (nunca `EXEC`, nunca DML/DDL) es sólido, está verificado arquitectónicamente (no solo declarado) y el framework nuevo no necesita ni debe tocarlo — Discovery Evidence/Confidence se calculan sobre lo que ya se lee, no requieren nuevas queries de escritura.
- **El patrón Read Model** (Priority & Complexity Engine, Data Dictionary, Dependency Graph — funciones puras de solo lectura sobre tablas ya pobladas, recalculadas en cada request) es el patrón correcto para Discovery/Resolution Coverage también — no hay que inventar una arquitectura nueva, hay que seguir aplicando la que ya se decidió en v0.5.
- **El upsert-by-name de `save_analysis()`** (preserva `review_status`/`review_notes` a través de re-análisis, vía ADR-0001) ya resuelve el problema de "contenido curado que sobrevive al re-análisis" — cualquier campo nuevo en `findings`/`unknowns` keyed por `app_name` hereda esta protección automáticamente sin trabajo adicional, siempre que se siga la misma convención.
- **La lista curada `KNOWN_UNREACHABLE_SERVERS`** ya es, en miniatura, el patrón correcto para el Failure Reason Catalog — generalizarla, no reinventarla.

---

## 9. Deuda técnica a resolver ANTES de implementar (no después)

En orden de bloqueo real (lo que sigue depende de que esto exista primero):

1. **Infraestructura de tests** (sección 5) — sin esto, ningún cambio a `extract.py` es seguro.
2. **Punto único de conversión dataclass↔fila-de-BD** (sección 2) — sin esto, cada campo nuevo (confidence, evidence, unknown_reason) se vuelve 3 cambios manuales sincronizados por dataclass, con alto riesgo de desincronización silenciosa.
3. **Captura de la causa en el momento de la extracción, no reconstruida después** (sección 1.1) — decisión de diseño que condiciona cómo se escribe TODO el código de las fases siguientes; si se empieza a implementar Unknowns antes de aceptar esto, se construye sobre la premisa equivocada.
4. **Código de causa estructurado en `enrich.py`, no solo texto** (sección 4) — bloquea el Failure Reason Catalog específicamente para conexiones (aunque no bloquea el resto del framework).

Lo que NO es deuda bloqueante (se puede convivir con esto mientras se implementa el resto):
- La unificación de `findings`/`security_flags`/`unknowns` (sección 7) — se mitiga con una forma compatible desde el diseño, no requiere migrar las tablas existentes ahora.
- El tamaño creciente de `db.py` (sección 6, tabla) — vale la pena decidir la convención (¿un paquete `db/` por dominio?) pero no bloquea agregar las funciones de Coverage a este archivo en esta fase; es una decisión a documentar y posponer conscientemente, no a resolver ya.

---

## 10. Veredicto

La arquitectura actual es sólida para lo que fue diseñada (extracción + agregación de solo lectura) y no requiere un rediseño mayor. Pero el Validation Framework pedido **no es una capacidad más del mismo tipo que el Priority Engine** — es una capacidad que necesita información que la capa de extracción **nunca capturó porque nunca se le pidió**. Por eso el plan de implementación (`IMPLEMENTATION_PLAN.md`) empieza deliberadamente por infraestructura (tests, modelo de evidencia, catálogo de causas) antes de tocar ningún extractor — no porque sea más prudente en abstracto, sino porque la sección 1 de este documento demuestra que hacerlo en el orden contrario obliga a re-derivar información que ya se perdió.
