# Evaluación arquitectónica formal y Gap Analysis — QAPV-LegacyAppAnalyzer

**Fecha:** 12 de agosto de 2026
**Autor del análisis:** Claude (Cowork), a solicitud de Eduardo Herrera / AFL Global
**Estado:** Para revisión y aprobación. No contiene propuestas de código ni de implementación.
**Contrato de seguridad de referencia:** el especificado por el usuario (no modificar artefactos legacy, solo lectura contra bases de datos legacy, no ejecutar SPs no verificados como read-only, no side effects, enmascarar credenciales).

---

## 0. Objetivo real y alcance de esta evaluación

> "Capturar suficiente evidencia de una aplicación legacy .NET, a partir del ejecutable/decompilado disponible, para permitir que un desarrollador reconstruya una aplicación funcionalmente equivalente sobre Ignition MES sin acceso al código fuente original."

El Analyzer es una herramienta de **extracción de evidencia**, no de modificación, ejecución productiva ni migración automática. Esta evaluación se basa en evidencia real: lectura directa del código (`extract.py`, `db.py`, `db_introspect.py`, `enrich.py`, `decompile.py`, `report.py`, tests), los documentos de arquitectura/ADRs/límites conocidos, consultas de solo lectura contra `qapv_analyzer.db` (la base propia del Analyzer, no una base legacy), y una inspección real de una carpeta ya decompilada (`decompiled/DataTransfer`). No se propone código nuevo en este documento.

---

## 1. Verificación del contrato de seguridad sobre el código actual

Antes de cualquier gap analysis, se verificó de primera mano si el código existente ya respeta el contrato. Resultado: **sí, con una salvedad importante señalada al final de esta sección.**

**`analyzer/db_introspect.py`** — declara en su docstring un "STRICT INVARIANT": toda función emite únicamente `SELECT` contra catálogos de sistema (`sys.*`, `INFORMATION_SCHEMA.*`) o funciones de metadatos de solo lectura (`OBJECT_DEFINITION`, `sys.dm_exec_describe_first_result_set_for_object` — esta última **describe estáticamente** el resultado de un SP vía el plan de ejecución, sin ejecutarlo). Se leyó el archivo completo: no hay ningún `INSERT`/`UPDATE`/`DELETE`/`EXEC`/`ALTER`/`CREATE`/`DROP`. La conexión se abre con `ApplicationIntent=ReadOnly` como hint adicional (no como única garantía, según el propio comentario del código). **Ningún stored procedure se ejecuta jamás** — solo se lee su definición de texto (`OBJECT_DEFINITION`) y su firma declarada (`sys.parameters`), nunca su comportamiento.

**`analyzer/enrich.py`** — orquesta `db_introspect.py` sin añadir ninguna capacidad de escritura propia. Decide qué SPs/tablas buscar a partir de evidencia ya extraída estáticamente (`sql_findings`, `settings`), nunca ejecuta nada del legado. Falla de forma controlada (agrega a `connection_errors`) ante timeouts/permisos/servidores decomisionados, sin nunca asumir comportamiento no verificado.

**`analyzer/decompile.py`** — invoca `ilspycmd` solo para leer el ensamblado original (`assembly_path`) y escribir el árbol de fuentes decompilado en un `output_dir` separado (carpeta propia del Analyzer). No se encontró ninguna operación de escritura, renombrado o borrado sobre `assembly_path` ni sobre ningún artefacto original.

**Credenciales** — `db_introspect.parse_dotnet_connection_string` extrae `password`/`pwd` de connection strings para poder conectarse, y las mantiene solo en memoria dentro de la llamada; no se encontró ningún `print`/`log` de esos valores. Sin embargo, **sí existe una limitación conocida (L1) confirmada con evidencia real**: `AlmacenDiagnostico/Program.cs:230` contiene una credencial de producción en texto plano que hoy queda expuesta sin enmascarar en el propio archivo fuente decompilado (esto es evidencia del legado, no una fuga causada por el Analyzer) — pero no hay hoy un mecanismo explícito de enmascarado automático en los reportes/exports para *cualquier* secreto detectado; depende de que el extractor lo reconozca como `is_connection_string`/categoría `sql_or_oracle` primero.

**Salvedad importante — no hay ninguna prueba automatizada que garantice el contrato.** Se revisó `tests/test_decompile_blocklist.py` (verifica que ciertos ensamblados de terceros no se decompilen, no verifica ausencia de escritura) y el resto de la suite: **ningún test falla si en el futuro alguien agrega una línea `EXEC`, un `INSERT` contra una BD legacy, o una escritura sobre `assembly_path`.** La garantía hoy es 100% de disciplina de código y revisión humana (docstrings, convención de nombres de módulo), no de verificación automática. Esto es una brecha real frente al punto 7 del "Analyzer Safety Contract" del usuario ("Tests MUST fail if a code path attempts a prohibited operation") — se retoma en la Sección 7, pregunta 12.

**Conclusión de la sección 1:** el código actual **es compatible** con el contrato de seguridad tal como está escrito hoy. El riesgo no es una violación existente, sino la ausencia de un mecanismo automático que impida una violación futura.

---

## 2. Niveles de capacidad

| Nivel | Qué permite | Evidencia mínima que el Analyzer debe producir |
|---|---|---|
| **L1 — Portfolio understanding** | Saber qué aplicaciones existen, qué tan grandes/riesgosas son, y sus dependencias de alto nivel (BD, servidores, stack). | Identidad de la app, stack tecnológico, lista de conexiones/servidores, conteo y severidad de hallazgos de seguridad, prioridad/complejidad relativa dentro del portafolio. |
| **L2 — Application reconstruction** | Que un desarrollador de Ignition, sin ver el código original, pueda reconstruir la funcionalidad principal: pantallas, flujo, reglas de negocio, acceso a datos, integraciones, reportes. | Todo lo de L1, más: inventario de pantallas/controles/validaciones, flujo de navegación, catálogo de reglas de negocio no triviales, modelo de datos real (no solo inferido de SQL), definición completa de cada SP/query usado, integraciones no-SQL detectadas, catálogo de reportes/exportaciones generadas por el legado. |
| **L3 — Functional equivalence** | Que se pueda *validar* que la reconstrucción se comporta igual que el legado (no solo que "se parece"). | Todo lo de L2, más: casos de prueba de referencia (entrada→salida esperada) derivados de observación real del legado, criterios de aceptación explícitos por pantalla/flujo, y un mecanismo de comparación legado-vs-reconstruido. |

**Evaluación honesta:** el estado actual del Analyzer cubre **L1 de forma sólida** (con matices, ver Sección 3.1) y **partes de L2 relacionadas con datos** (SQL, conexiones, SPs). **No cubre L2 en UI/navegación/lógica de negocio ni L3 en absoluto.** L3 no debería ser objetivo del Analyzer como herramienta estática — se retoma en la Sección 7.

---

## 3. Gap analysis por área (evidencia real)

Para cada área: **captura actual**, **dónde** (archivo/tabla), **evidencia real** (cifras del portafolio, cuando aplica), **qué falta**, **clasificación de recuperación** (A=estático, B=introspección BD read-only, C=observación runtime read-only, D=revisión humana, E=no recuperable de forma confiable), **prioridad para L2**, **mínimo viable**, **riesgos**.

### 3.1 Application identity

- **Captura actual:** tabla `apps` (`id, name, source_path, analyzed_at, dotnet_target, ui_framework, db_drivers, companion_assemblies, db_intro_notes, review_status, review_notes`). `save_analysis()` busca una app existente por `source_path` y, si no la halla, por `name`; si existe, hace **`DELETE FROM apps WHERE name=?` seguido de `INSERT`** (no `UPDATE`).
- **Evidencia real verificada en el esquema (`db.py` líneas 18-27, 251-308) y confirmada contra `qapv_analyzer.db`:** no existe columna `identity_id`, `confidence_score` ni `resolution_status` en ninguna tabla ni migración (`ALTER TABLE`) del proyecto. 105 apps en la base; `review_status`: 80 `logica_revisada`, 8 `borrador`, 17 `obsoleta`.
- **Gap real identificado:** los ADR-0000/0001/0002 (aprobados) describen un `identity_id` estable y desacoplado, con estado `New/Resolved/Candidate` y `confidence_score` — **ninguno de los tres está implementado en el código real.** La identidad sigue dependiendo de `name`/`source_path` como en el diseño que los ADR dicen descartar, y cada reanálisis sigue siendo un `DELETE`+`INSERT` con un `apps.id` nuevo cada vez (autoincremental), no una actualización sobre un identificador persistente.
- **Consecuencia práctica confirmada leyendo `app.py` (líneas 60-77):** como `db_procedures`/`db_tables` tienen `ON DELETE CASCADE` sobre `apps.id`, y `apps.id` cambia en cada reanálisis, **si la introspección de BD falla en un reanálisis puntual (servidor caído, VPN, etc.), la evidencia real de esquema obtenida en un análisis anterior se pierde sin restaurarse** — `save_db_objects()` solo se invoca dentro del `else` del `try/except` alrededor de `enrich_app()`; si `enrich_app` lanza excepción, esa llamada nunca ocurre. Esto no se ha observado empíricamente en este análisis (no se forzó ese escenario), pero es una consecuencia directa y verificable del código, relevante precisamente porque los servidores legacy se van a decomisionar como parte de la migración (ya ocurrió con `naamrt-qcs11`).
- **Recuperación:** A (el propio dato ya existe en el código, es un problema de diseño de persistencia, no de extracción).
- **Prioridad para L2:** Alta — no bloquea reconstruir una app individual hoy, pero sí bloquea confiar en el histórico acumulado del portafolio a medida que se reanaliza repetidamente.
- **Mínimo viable:** un identificador estable que sobreviva reanálisis, y que la introspección de BD (cara de obtener, potencialmente irrepetible una vez decomisionado el servidor origen) se trate como dato curado, no regenerable.
- **Riesgo de intentar resolverlo:** ninguno relacionado con el legado — es un cambio interno al Analyzer.

### 3.2 UI / pantallas / controles

- **Captura actual:** ninguna. `extract.py` no tiene ningún trigger que reconozca declaraciones de controles UI (botones, labels, grids, menús) ni la construcción de layout.
- **Evidencia real (inspección directa de `decompiled/DataTransfer`):** ilspycmd, al decompilar IL (no código fuente), **fusiona las partial classes** de cada formulario — la lógica escrita a mano y el `InitializeComponent()` generado por el diseñador de Visual Studio (que en el código fuente original vive en `Form.Designer.cs`) terminan en el **mismo archivo `.cs` decompilado**, porque esa separación es un artefacto de código fuente que no existe a nivel de IL. Se confirmó esto por tamaño: `FrmRework.cs` (194,837 bytes), `frmValida.cs` (38,401 bytes), `FrmReworkGeo.cs` (35,494 bytes) son órdenes de magnitud más grandes que archivos de solo-lógica comparables — consistente con contener el árbol completo de construcción de controles (posición, tamaño, texto, wiring de eventos) además de la lógica de negocio.
- **Qué falta:** ningún extractor lee ese `InitializeComponent()` para catalogar controles/textos/eventos por pantalla. La información **ya está en los archivos que hoy se decompilan**, simplemente no se procesa.
- **Recuperación:** **A (estático)** — es la conclusión más importante de esta área: no se necesita ejecutar la app ni observarla en runtime para obtener el inventario base de controles; ya está en el código decompilado existente.
- **Prioridad para L2:** **Crítica** — sin esto, "reconstrucción" real es imposible más allá de la capa de datos.
- **Mínimo viable:** por pantalla: tipo de control, texto/label, nombre de variable, y el método manejador de evento asociado (para poder cruzarlo con lógica de negocio, área 3.4).
- **Riesgos:** ninguno de seguridad — es análisis estático sobre código ya decompilado sin tocar el legado en ejecución. Riesgo de ingeniería: WPF (34 apps puras + 18 mixtas de 105, ver 3.othe stack) usa XAML embebido como recurso, no `InitializeComponent()` en C# — la técnica de extracción para WinForms no sirve igual para WPF; se necesitaría una segunda vía específicamente para recursos XAML.

### 3.3 Navegación y flujos de usuario

- **Captura actual:** ninguna.
- **Evidencia real:** no existe ningún extractor que rastree qué formulario abre a cuál (`.Show()`, `.ShowDialog()`, navegación entre `Page`/`Frame` en WPF), ni el orden de aparición de pantallas.
- **Qué falta:** todo. Esta información vive dispersa en los manejadores de eventos de botones/menús (p. ej. `btnGuardar_Click` que abre `new FrmRework().ShowDialog()`), la cual sí está en el código decompilado ya disponible, pero no hay ningún patrón reconocido para ella hoy.
- **Recuperación:** A (estático) para el grafo de invocación entre formularios (buscar instanciaciones de clases que heredan de `Form`/`Window` dentro de manejadores de evento); D (revisión humana) para confirmar el *propósito* de negocio de cada transición, que un grafo de llamadas no puede inferir por sí solo.
- **Prioridad para L2:** Alta.
- **Mínimo viable:** un grafo dirigido pantalla→pantalla con el evento disparador, sin necesidad de inferir la razón de negocio (eso queda para revisión humana).
- **Riesgos:** ninguno — puramente estático.

### 3.4 Lógica de negocio

- **Captura actual:** ninguna fuera del contexto SQL. `extract.py` solo reconoce `SQL_TRIGGER`, `LOCAL_IO_TRIGGER` y `REFLECTION_UNAMBIGUOUS`/`INVOKE_BARE` — cálculos, condicionales y reglas de negocio generales (p. ej. "si el turno es nocturno, aplicar este factor") no tienen ningún patrón que los reconozca.
- **Evidencia real:** confirmado por lectura directa de `extract.py` (961 líneas): no hay ninguna estructura que capture expresiones aritméticas, condicionales de negocio o máquinas de estado. `analyzer/failure_catalog.py` y `analyzer/unknown.py` existen pero **no son importados por ningún módulo de producción** (confirmado por grep) — son infraestructura sin conectar, no capturan lógica de negocio real hoy.
- **Qué falta:** todo. Esta es, junto con UI, la brecha más seria para L2, y la más difícil de resolver de forma puramente estática: una regla de negocio puede estar expresada como código C# arbitrariamente complejo, no como un patrón léxico reconocible de forma confiable con regex (el propio diseño de `extract.py` renuncia deliberadamente a "reimplementar un lexer completo de C#").
- **Recuperación:** **D (revisión humana), asistida por A** — lo realista no es "extraer la regla de negocio automáticamente" sino que el Analyzer **señale candidatos** (métodos con complejidad ciclomática/aritmética no trivial fuera de los triggers ya conocidos) para que un humano los revise, en vez de que desaparezcan sin rastro como ocurre hoy.
- **Prioridad para L2:** Crítica, pero con expectativa realista: no es automatizable al 100% de forma segura y confiable; el valor del Analyzer aquí es *señalar dónde mirar*, no *decidir qué significa*.
- **Mínimo viable:** lista de métodos "con lógica no trivial no clasificada" por app, con snippet y ubicación, para revisión humana dirigida en vez de lectura manual de todo el archivo.
- **Riesgos:** ninguno de seguridad. Riesgo de falsa confianza: si se implementa mal, puede dar la impresión de que "ya se extrajo la lógica de negocio" cuando en realidad solo se señalaron candidatos — debe documentarse así explícitamente para no violar el principio de "nunca inferir silenciosamente" (P3).

### 3.5 Conexiones a base de datos

- **Captura actual:** sólida. `extract.py` detecta connection strings en `app.config` (XML real) y `Settings.cs` (`DefaultSettingValue`), con deduplicación.
- **Evidencia real:** 597 `settings` en 105 apps; 130 categorizadas `sql_or_oracle`; 76 apps distintas tienen al menos un setting.
- **Qué falta:** connection strings declaradas como **campo de clase** (no en `Settings.cs`/`app.config`) son invisibles — confirmado un caso real de severidad alta: `AlmacenDiagnostico/Program.cs:230` (credencial de producción en texto plano, limitación L1, P0, sin resolver).
- **Recuperación:** A (estático) — el gap es de cobertura del patrón de extracción, no de técnica.
- **Prioridad para L2:** Alta (y de seguridad).
- **Mínimo viable:** cubrir declaraciones de campo de clase con literal de connection string, no solo los dos mecanismos actuales.
- **Riesgos:** ninguno para el legado; el riesgo es de exposición de credenciales si el reporte no las enmascara — verificar antes de distribuir reportes fuera del equipo de análisis.

### 3.6 Consultas SQL

- **Captura actual:** fuerte para SQL estático/concatenado lineal; documentado como incompleto para SQL con ramificación condicional.
- **Evidencia real:** 5,276 `sql_findings` en el portafolio; categorías `query` (4,873), `stored_procedure` (400), `oracle_package_call` (3). **Exactamente el 50% (2,638 de 5,276) tiene `target IS NULL`** — es decir, la mitad de todo el SQL detectado en el portafolio no tiene una tabla/objeto identificado, ya sea porque genuinamente no aplica (p. ej. un `SELECT 1`) o porque es SQL dinámico no resuelto — y `ARCHITECTURE_REVIEW.md` señala que estos dos casos hoy se mezclan sin distinguirse en el dato mismo.
- **Qué falta:** SQL con `StringBuilder` + ramificación (`if`/`else`/ternario) — caso real confirmado en `SGI`; distinguir "sin tabla" de "sin resolver".
- **Recuperación:** A (estático) para el caso de ramificación acotada a un método; **E (no recuperable de forma confiable)** para SQL cuyo texto depende de datos de runtime/entrada de usuario que ningún análisis estático puede predecir — en esos casos, lo correcto es marcar `UNKNOWN` explícito, no simular ejecución.
- **Prioridad para L2:** Alta.
- **Mínimo viable:** que el 50% con `target NULL` se reclasifique explícitamente en la taxonomía NOT_FOUND / NOT_ANALYZED / UNKNOWN (ver sección 6) en vez de quedar indiferenciado.
- **Riesgos:** ninguno — es análisis estático sobre texto ya decompilado.

### 3.7 Stored procedures

- **Captura actual:** clasificación correcta implementada (Incremento 4, 110 tests). Definición real, parámetros y forma del result set **cuando la introspección de BD tiene éxito**.
- **Evidencia real:** 400 `sql_findings` categorizados como `stored_procedure`; de la introspección real contra BD (`db_procedures`), **99 filas para solo 16 de 105 apps** (98 con definición obtenida, 1 `not_found`) — la introspección se intenta automáticamente en cada análisis (confirmado en `app.py` líneas 40-77), pero solo tiene éxito para ~15% de las apps del portafolio en la práctica, probablemente por conectividad (VPN, servidores decomisionados, permisos).
- **Qué falta:** para el 85% restante de apps, no hay definición real de sus SPs — solo el nombre y el sitio de la llamada extraídos estáticamente del código.
- **Recuperación:** **B (introspección BD read-only)** — ya implementada correctamente y de forma segura; el gap es de **alcance/conectividad**, no de técnica ni de seguridad. Ejecutar el SP para "ver qué hace" está y debe seguir estando fuera de alcance (violaría el contrato).
- **Prioridad para L2:** Alta.
- **Mínimo viable:** cuando la introspección falla, que quede registrado como `UNKNOWN — SP definition not retrievable (connection/permission)`, no como ausencia silenciosa.
- **Riesgos:** el mecanismo actual ya es seguro (solo `OBJECT_DEFINITION`/catálogos). El riesgo real está en el punto 6 del contrato del usuario: **nunca asumir que un SP es de solo lectura por su nombre** — hoy el Analyzer no ejecuta ningún SP, así que no hay violación, pero tampoco hay ninguna prueba que impida que un futuro incremento agregue un `EXEC` "solo para probar" — coincide con el hallazgo de la Sección 1.

### 3.8 Modelo de datos / esquema

- **Captura actual:** vistas de portafolio (`vw_table_dictionary`, `vw_dependency_graph`) construidas sobre lo que aparece en `sql_findings`/`db_tables`; columnas reales, tipos, nulabilidad y FKs **cuando** la introspección tuvo éxito.
- **Evidencia real:** `db_tables` tiene 431 filas para 43 de 105 apps (~41%) — mejor cobertura que SPs (probablemente porque una tabla referenciada por una app puede residir en un servidor más accesible, o porque el fallback por conexión parcial aún captura algunas tablas antes de fallar en otras).
- **Qué falta:** para el 59% restante de apps, el "modelo de datos" es solo lo que el SQL literal menciona — sin tipos de columna, constraints ni relaciones reales.
- **Recuperación:** B (introspección BD read-only), ya implementada; gap de alcance.
- **Prioridad para L2:** Crítica — sin esquema real, cualquier tabla recreada en Ignition MES es una suposición.
- **Mínimo viable:** garantizar que la introspección se reintente o se marque explícitamente como pendiente por app, en vez de perderse silenciosamente en un reanálisis fallido (ver 3.1).
- **Riesgos:** ninguno nuevo — mismo mecanismo ya verificado como read-only.

### 3.9 Integraciones externas (no-SQL)

- **Captura actual:** Reflection/COM/CLSID/Modbus (Fase 4, cerrada para L16/L18, L17 "mitigada"). Todo lo demás — colas MSMQ, servicios SOAP/WCF, FTP, EF/Dapper/LINQ-to-SQL — está catalogado en `KNOWN_LIMITATIONS.md` como "sentinela, sin evidencia", es decir, **deliberadamente sin implementar** porque no se había confirmado su presencia real en el portafolio.
- **Evidencia real nueva de este análisis:** al inspeccionar `decompiled/DataTransfer` se encontró evidencia real y confirmada de una integración no cubierta hoy por ningún trigger: uso de **Microsoft.Office.Interop.Excel** (automatización COM de Excel) y de una librería de generación de PDF de terceros (**QuestPDF**) — ninguna de las dos es SQL, Reflection puro ni Modbus, y ninguna aparece en `KNOWN_LIMITATIONS.md` como sentinela específico. Esto confirma, con evidencia real y no teórica, que el catálogo de integraciones "sentinela" del proyecto está incompleto frente al portafolio real.
- **Recuperación:** A (estático) para detectar el *uso* de la integración (imports, tipos instanciados); D (revisión humana) para documentar el comportamiento exacto esperado.
- **Prioridad para L2:** Alta — cualquier integración no detectada es una funcionalidad que se perderá silenciosamente en la reconstrucción si nadie la nota.
- **Mínimo viable:** al menos *marcar la presencia* de la integración (biblioteca detectada + ubicación), aunque no se resuelva el detalle — igual que se pidió para el punto 3.4.
- **Riesgos:** ninguno — es detección estática de imports/tipos ya presentes en el código decompilado.

### 3.10 File I/O

- **Captura actual:** 879 `io_findings`, todos bajo una única categoría genérica `io` (confirmado en el esquema: `io_findings.category` tiene un solo valor real en todo el portafolio).
- **Qué falta:** no se distingue lectura de escritura, ni ruta relativa/absoluta, ni si el archivo es de configuración/datos/log/salida de reporte.
- **Recuperación:** A (estático) — es un problema de granularidad de clasificación, no de técnica.
- **Prioridad para L2:** Media.
- **Mínimo viable:** al menos separar lectura vs. escritura y si la ruta es configurable o fija.
- **Riesgos:** ninguno.

### 3.11 Reportes / exportaciones / documentos generados

- **Captura actual:** el Analyzer genera *sus propios* reportes de análisis (`.md`, `.xlsx`, `.docx`, diagrama Mermaid) — no cataloga los reportes que **la app legacy misma** genera para el negocio.
- **Evidencia real muy relevante (inspección de `decompiled/DataTransfer`):** se encontraron **definiciones de reporte RDLC originales extraídas como recurso** (`DataTransfer.Report_1310_1550.rdlc`, 84,214 bytes; `DataTransfer.Report_850.rdlc`, 58,974 bytes) — estos son los layouts XML reales de Microsoft ReportViewer embebidos en el ensamblado original, recuperados **tal cual, sin decompilar código, como recurso**, por `ilspycmd`. Además existe un motor de reportes propio in-house (`Reportador/ReportParser.cs`, `ReportGenerator.cs`, `ReportBase.cs`) con su propio formato de definición. Esto significa que, para al menos esta app, **el layout exacto de los reportes de negocio ya está disponible sin ningún trabajo adicional** — simplemente no se está copiando ni catalogando hoy.
- **Qué falta:** ningún paso del pipeline copia, indexa ni menciona estos recursos `.rdlc` (ni equivalentes de otras apps) como parte del paquete de evidencia entregable.
- **Recuperación:** **A (estático)** — es directamente un archivo de recurso ya extraído, no requiere ni siquiera parsear código.
- **Prioridad para L2:** Alta — para el negocio, "qué reportes imprime esta app y con qué formato" suele ser tan importante como la lógica interna.
- **Mínimo viable:** un paso que localice y copie/catalogue archivos de definición de reporte conocidos (`.rdlc`, y el formato propio de `Reportador` si aplica) junto al resto de la evidencia de cada app.
- **Riesgos:** ninguno — son archivos de recurso ya extraídos de forma no destructiva por el pipeline existente.

### 3.12 Configuración

- **Captura actual:** cubierta razonablemente bien vía `settings` (597 filas), aunque limitada a `Settings.cs`/`app.config` (ver 3.5 para el gap de campos de clase).
- **Qué falta:** lo mismo que 3.5; no hay gap adicional específico de "configuración" no cubierto ya por esa área.
- **Recuperación:** A.
- **Prioridad:** Media (ya cubierta en su mayoría).
- **Mínimo viable:** ya alcanzado salvo el gap de 3.5.
- **Riesgos:** ninguno.

### 3.13 Comportamiento sensible a seguridad

- **Captura actual:** sólida — `security.py` detecta contraseñas en texto plano y SQL concatenado sin parametrizar.
- **Evidencia real:** 927 `security_flags`; 88 de severidad `alta`, 836 `media`, 3 `info`.
- **Qué falta:** el caso de campo de clase (3.5/L1) sigue sin detectarse pese a ser el hallazgo de mayor severidad real conocido del portafolio.
- **Recuperación:** A.
- **Prioridad:** Crítica (ya es alta hoy; cerrar el gap de 3.5 la completa).
- **Mínimo viable:** cerrar 3.5.
- **Riesgos:** el reporte de estos hallazgos debe seguir tratándose como evidencia sensible internamente (contrato punto 4) — no se encontró que el Analyzer los publique fuera de su propia base/reportes internos, pero vale la pena confirmarlo como política explícita antes de compartir reportes ampliamente.

### 3.14 Comportamiento en runtime

- **Captura actual:** ninguna. El Analyzer es 100% estático hoy — no ejecuta ninguna app legacy.
- **Qué falta:** comportamiento que depende de datos de entrada reales, temporización, condiciones de carrera, hardware conectado (Modbus, dispositivos de planta) — nada de esto es observable sin ejecutar la app.
- **Recuperación:** **C (observación runtime read-only), y solo bajo las condiciones del contrato de seguridad del usuario** — el propio contrato exige demostrar aislamiento total antes de cualquier ejecución, y prohíbe cualquier side effect (transacciones, inventarios, equipos industriales). Dado que varias de estas apps interactúan con **equipos de planta** (Modbus confirmado, dispositivos de laboratorio de fibra óptica por los nombres de proyecto: `OTDR`, `PullTest`, `GeoStats*`), el riesgo de side effect físico es real y no hipotético.
- **Prioridad para L2:** Baja como *requisito* (L2 no exige equivalencia funcional, solo reconstrucción de la funcionalidad principal); **sería la base de L3** si se decide perseguirlo eventualmente, y solo en un entorno de laboratorio aislado, nunca contra producción.
- **Mínimo viable para L2:** ninguno — L2 puede alcanzarse sin análisis dinámico.
- **Riesgos:** altos si se hace sin aislamiento verificado — coincide exactamente con la restricción #3 del contrato del usuario. **Recomendación: fuera de alcance del Analyzer hasta que exista un entorno de laboratorio aislado explícitamente aprobado.**

### 3.15 Evidence / Confidence / Unknowns

- **Captura actual:** el framework `Evidence`/`Confidence` (línea, snippet, extractor, patrón, nivel de confianza) está bien implementado y usado consistentemente en `settings` y parte de `sql_findings`. El `Unknowns Engine` (`unknown.py`) existe como forma de dato pero **la tabla `unknowns` tiene 0 filas en todo el portafolio** — confirmado por consulta directa: está construido y no se usa en absoluto.
- **Evidencia real adicional:** la tabla `findings` (conocimiento curado por humanos, 222 filas) sí tiene un ciclo de vida real (`status`, `status_changed_at/by`) — la disciplina de curaduría humana existe y se usa para hallazgos, pero no para "vacíos de conocimiento" (unknowns), que es justamente la pieza que el propio framework de validación identifica como necesaria para no "inferir silenciosamente" (P3).
- **Qué falta:** conectar `failure_catalog.py`/`unknown.py` a `enrich.py`/`extract.py`/`report.py` para que cuando algo no se resuelva, se registre como Unknown real en vez de un mensaje genérico de texto libre (ya documentado como deuda en `KNOWN_LIMITATIONS.md` L21).
- **Recuperación:** A — es trabajo de integración de piezas ya existentes, no de investigación nueva.
- **Prioridad para L2:** Alta — es la pieza que sostiene la honestidad de todo lo demás.
- **Mínimo viable:** la taxonomía de la Sección 6 aplicada al menos a `sql_findings.target IS NULL` (2,638 filas hoy indiferenciadas) y a los resultados fallidos de introspección de BD.
- **Riesgos:** ninguno.

---

## 4. Matriz de cobertura

| Capability | Cobertura actual | Cobertura objetivo (L2) | Evidencia disponible | Confianza | Unknown si no resuelto | Complejidad de implementación | Valor de migración | Prioridad | Riesgo de seguridad |
|---|---|---|---|---|---|---|---|---|---|
| Application identity | Baja (name/source_path, sin identity_id real) | Alta | Código (`db.py`) | Alta (el gap es cierto, no una hipótesis) | Se pierde histórico entre reanálisis | Media | Alta (integridad del portafolio) | Alta | Ninguno |
| UI / screens / controls | Nula | Alta | Código decompilado ya existente (confirmado con `DataTransfer`) | Alta (evidencia real de que es factible) | Sin captar, no hay registro de qué pantallas existen | Alta (nuevo extractor, WinForms primero, WPF/XAML aparte) | Crítico | Crítica | Ninguno (estático) |
| Navegación / flujos | Nula | Media-Alta | Código decompilado ya existente | Media | Sin grafo de navegación | Media | Alto | Alta | Ninguno (estático) |
| Lógica de negocio | Nula (fuera de SQL) | Media (asistida, no automática) | Código decompilado ya existente | Baja (intrínsecamente difícil de automatizar bien) | Reglas se pierden sin rastro | Alta (o media si se limita a "señalar candidatos") | Crítico | Crítica | Ninguno (estático) |
| Conexiones a BD | Alta | Alta | 597 settings, 76 apps | Alta | 1 caso P0 confirmado sin resolver (campo de clase) | Baja | Alto | Alta | Ninguno (estático); alto si no se enmascara al compartir |
| Consultas SQL | Media-Alta | Alta | 5,276 sql_findings; 50% target NULL | Media (mitad sin target) | 2,638 findings indiferenciados | Media | Alto | Alta | Ninguno (estático) |
| Stored procedures | Media (técnica OK, alcance bajo) | Alta | 400 findings; 99 definiciones reales para 16/105 apps | Alta donde hay introspección; Unknown en el resto | 85% de apps sin definición real de sus SPs | Baja (ya implementado; falta alcance) | Alto | Alta | Ninguno (ya read-only verificado) |
| Modelo de datos / esquema | Media (41% de apps con esquema real) | Alta | 431 db_tables para 43/105 apps | Alta donde hay introspección | 59% de apps sin esquema real | Baja (ya implementado; falta alcance + persistencia) | Crítico | Crítica | Ninguno (ya read-only verificado) |
| Integraciones externas | Baja (solo Reflection/COM/Modbus parcial) | Media-Alta | Confirmado uso real de Excel Interop y QuestPDF no catalogado | Media | Integraciones reales sin marcar (ni como sentinela) | Media | Alto | Alta | Ninguno (estático) |
| File I/O | Media (detecta, no clasifica) | Media | 879 io_findings, sin subcategoría | Media | Sin distinguir lectura/escritura | Baja | Medio | Media | Ninguno (estático) |
| Reportes / exports del legado | Nula (no se catalogan) | Alta | .rdlc reales ya extraídos como recurso (confirmado) | Alta (evidencia real de factibilidad inmediata) | Reportes de negocio no documentados | Baja (son archivos ya extraídos, falta catalogarlos) | Alto | Alta | Ninguno (estático) |
| Configuración | Alta | Alta | Cubierta por 3.5 | Alta | Igual que 3.5 | — | Medio | Media | Ninguno |
| Seguridad | Alta | Alta | 927 security_flags | Alta | 1 gap P0 conocido (campo de clase) | Baja | Alto | Crítica | Ninguno (estático); enmascarado pendiente de política explícita |
| Runtime | Nula | No requerido para L2 | N/A | N/A | N/A | Alta y sensible | Bajo para L2 / Alto para L3 | Baja (para L2) | **Alto si no está aislado** |
| Evidence/Confidence/Unknown | Media (Evidence sí, Unknown no usado) | Alta | 0 filas en `unknowns` confirmado | N/A | Todo lo no resuelto se pierde como texto libre | Baja (integrar piezas existentes) | Alto (transversal) | Alta | Ninguno |

---

## 5. Static vs. Dynamic — resumen de clasificación

| Técnica | Aplica a | Estado |
|---|---|---|
| **A — Análisis estático** | UI/controles, navegación, señalización de lógica de negocio, integraciones no-SQL, catalogación de reportes (.rdlc), granularidad de I/O, identidad (fix de diseño interno) | **Preferido y suficiente para casi todo el gap de L2.** Es la vía de menor riesgo y ya está probada por este mismo proyecto (todo el extractor actual es estático). |
| **B — Introspección de BD read-only** | Definición real de SPs, esquema real de tablas | **Ya implementada correctamente y de forma segura.** El gap es de alcance (conectividad) y de persistencia (se pierde en reanálisis fallidos), no de técnica ni de permiso. |
| **C — Observación runtime read-only** | Comportamiento dependiente de datos/temporización, validación de equivalencia funcional (L3) | **No implementada. No recomendada todavía.** Requiere demostrar aislamiento total antes de considerarse, dado que varias apps del portafolio interactúan con equipos físicos de planta. |
| **D — Revisión humana** | Propósito de negocio de cada flujo, validación de reglas señaladas por el Analyzer, decisión sobre candidatos ambiguos | Ya existe un flujo real para esto (`review_status`, `findings` con `status`) — 80 de 105 apps ya pasaron por revisión humana de lógica. Falta extenderlo a UI/navegación/reglas de negocio, hoy solo cubre lo que el Analyzer ya extrae. |
| **E — No recuperable de forma confiable** | SQL dinámico cuyo texto depende de entrada de usuario en tiempo real; comportamiento exacto de hardware de planta sin observarlo en vivo | Debe registrarse como `UNKNOWN` explícito — nunca inferido ni simulado. |

---

## 6. Disciplina Evidence / Confidence / Unknown

El principio (P3, ADR-0002) está bien enunciado pero **su implementación real es parcial**: `Evidence` sí se usa; `Unknown` como estructura de dato existe (`unknown.py`, tabla `unknowns`) pero tiene **cero registros reales** en todo el portafolio analizado hasta hoy.

Se recomienda adoptar formalmente la taxonomía de estados solicitada, aplicándola donde hoy solo hay silencio o mensajes de texto libre:

| Estado | Cuándo aplica hoy (ejemplos reales encontrados) |
|---|---|
| `NOT_FOUND` | Búsqueda ejecutada, objeto confirmado ausente (p. ej. `db_procedures.status='not_found'`, 1 caso real). |
| `NOT_ANALYZED` | Área nunca evaluada para esa app (p. ej. cualquier app cuyo `ui_framework` sea WPF no tiene ningún intento de extracción de UI, porque el extractor de UI no existe). |
| `NOT_SUPPORTED` | Técnica reconocida como fuera de alcance por diseño (p. ej. SQL dinámico con ramificación en `SGI`, documentado explícitamente). |
| `ANALYSIS_FAILED` | Se intentó y falló por causa externa (p. ej. las 78 fallas de test por fixtures faltantes reportadas en el análisis anterior; conexión a servidor decomisionado). |
| `UNKNOWN` | Evidencia insuficiente para concluir, sin intento de adivinar (p. ej. el 50% de `sql_findings` con `target NULL`, hoy indiferenciado — debería dividirse entre varios de estos estados en vez de quedar como un solo `NULL`). |
| `CONFIRMED_ABSENT` | Se verificó activamente que algo no existe (distinto de `NOT_FOUND` puntual) — no se encontró ningún caso real de esto implementado hoy; sería nuevo. |

---

## 7. Respuestas a las 12 preguntas

**1. ¿Cuál es el verdadero gap entre el estado actual y Level 2?**
Tres brechas concretas, no hipotéticas: (a) UI/controles/navegación — cobertura nula, pese a que la evidencia ya está en el código decompilado que hoy se genera; (b) lógica de negocio fuera de contexto SQL — cobertura nula, e intrínsecamente no 100% automatizable; (c) alcance incompleto de introspección de BD real (modelo de datos completo solo para 41% de apps, SPs reales solo para 15%) más un problema de persistencia que puede perder esa evidencia en reanálisis futuros. Adicionalmente, reportes/exportaciones del legado (RDLC y similares) no se catalogan pese a estar ya disponibles como recurso extraído.

**2. ¿Cuál es el verdadero gap entre Level 2 y Level 3?**
Todo lo relacionado con runtime: casos de prueba de referencia derivados de comportamiento real observado, y un mecanismo de comparación legado-vs-reconstruido. Esto requeriría análisis dinámico, que hoy el Analyzer no hace y que el contrato de seguridad del usuario condiciona a un aislamiento total demostrado — no existe ese entorno hoy.

**3. ¿Qué capacidades deben implementarse primero?**
En orden: (1) extractor de UI para WinForms (mayor valor, menor riesgo, evidencia ya confirmada de factibilidad estática); (2) catalogación de reportes/exports del legado (.rdlc y similares — ya extraídos, solo falta indexarlos, esfuerzo bajo y valor alto); (3) señalización de candidatos de lógica de negocio (no extracción completa); (4) ampliar cobertura de introspección de BD real y resolver su problema de persistencia entre reanálisis; (5) grafo de navegación entre pantallas.

**4. ¿Cuáles deben permanecer fuera del alcance del Analyzer?**
Cualquier ejecución del legado con posibilidad de side effects (transacciones, cambios de estado, equipos de planta); ejecución de cualquier stored procedure no verificado como read-only; cualquier operación de escritura contra bases de datos legacy; modificación de cualquier artefacto original. Y, con matiz: la *extracción automática y completa* de reglas de negocio — el Analyzer debe señalar candidatos, no pretender decidir su significado.

**5. ¿Qué capacidades deberían resolverse mediante análisis estático?**
Prácticamente todo el gap identificado hacia L2: UI/controles, navegación, señalización de lógica de negocio, integraciones no-SQL, catalogación de reportes, granularidad de I/O, y la corrección del modelo de identidad interno del Analyzer.

**6. ¿Cuáles requieren observación dinámica read-only?**
Ninguna es estrictamente necesaria para L2. Para L3 (si se decide perseguirlo), la validación de equivalencia funcional sí lo requeriría, y solo bajo aislamiento total demostrado — hoy no existe ese entorno ni se recomienda construirlo como parte del alcance actual.

**7. ¿Cuáles requieren intervención humana?**
Confirmar el propósito de negocio detrás de cada transición de pantalla; validar/interpretar los candidatos de lógica de negocio señalados por el Analyzer; decidir si una integración detectada (p. ej. Excel Interop, QuestPDF) es crítica o incidental; y, como ya ocurre hoy con 80 de 105 apps, la revisión de lógica en general. La intervención humana ya tiene un flujo de trabajo funcionando (`review_status`, `findings`) — la recomendación es extenderlo a las áreas nuevas, no crear uno desde cero.

**8. ¿Qué nuevos ADRs o cambios arquitectónicos serían necesarios?**
(a) Un ADR que reconcilie la implementación real de identidad con las decisiones ya aprobadas en ADR-0000/0001/0002, o que las revise si ya no aplican — hoy hay una divergencia real entre lo decidido y lo implementado. (b) Un ADR sobre el ciclo de vida de la evidencia de introspección de BD (¿es regenerable o curada? — la Sección 3.1 muestra que tratarla como puramente regenerable es riesgoso una vez que los servidores legacy empiecen a decomisionarse). (c) Un ADR que defina el alcance y los límites explícitos de un futuro extractor de UI/navegación/lógica de negocio, incluyendo qué se automatiza y qué se señala para humano. (d) Un ADR que formalice la taxonomía Evidence/Confidence/Unknown de la Sección 6 como estándar del proyecto, no solo como intención documental.

**9. ¿Qué parte del roadmap actual debe cambiarse?**
El roadmap actual (Fases 2 a 8 de `IMPLEMENTATION_PLAN.md`) se enfoca casi exclusivamente en profundizar la calidad de la extracción de SQL/conexiones — que ya es la parte mejor cubierta del proyecto. Dado el objetivo real de reconstrucción, se recomienda insertar como nuevas fases de igual o mayor prioridad: UI/controles, navegación, catalogación de reportes del legado, y señalización de lógica de negocio — antes de seguir refinando el detalle de SQL dinámico con ramificación (Fase 3 restante), que tiene menor impacto relativo sobre el objetivo de "reconstrucción" que sobre el objetivo de "cero SQL sin resolver".

**10. ¿Cuál debería ser el siguiente incremento después del actual?**
Un incremento de **descubrimiento** (no de implementación todavía): tomar 2-3 apps representativas ya decompiladas (una WinForms, una WPF, una Console/Service) y catalogar manualmente qué información de UI/navegación/reportes contienen realmente sus fuentes decompiladas — para dimensionar con evidencia real (no estimación) el tamaño y la dificultad real de un futuro extractor de UI, igual que se hizo en este documento con `DataTransfer`, antes de comprometerse a construir nada.

**11. ¿Qué controles de seguridad deben convertirse en reglas permanentes del proyecto?**
Los diez puntos del "Analyzer Safety Contract" del usuario, tal como están redactados, deberían incorporarse literalmente como un documento permanente del proyecto (p. ej. `SAFETY_CONTRACT.md` en la raíz, referenciado desde `ARCHITECTURAL_PRINCIPLES.md`), no solo como instrucción de esta conversación.

**12. ¿Qué pruebas deben demostrar que el Analyzer jamás realiza operaciones de escritura contra sistemas legacy?**
Hoy: ninguna prueba automatizada lo hace — es una brecha real confirmada en la Sección 1. Se recomienda (sin implementarlo aún, conforme a lo solicitado): pruebas que parcheen la conexión ODBC con un doble de prueba que falle si se invoca cualquier método distinto de `execute` con una sentencia que no empiece por `SELECT`, o que inspeccione estáticamente el texto de cada `cur.execute(...)` en `db_introspect.py` y falle el build si aparece una palabra clave de escritura (`INSERT`, `UPDATE`, `DELETE`, `EXEC`, `ALTER`, `CREATE`, `DROP`, `MERGE`, `TRUNCATE`). Ambas son pruebas de contrato, no de comportamiento con BD real, y no requieren tocar ningún sistema legacy para ejecutarse.

---

## 8. Siguiente paso propuesto

No se propone ningún código. El siguiente paso, si se aprueba, sería el incremento de descubrimiento de la pregunta 10 (Sección 7) — puramente de lectura y catalogación manual/asistida sobre apps ya decompiladas, sin tocar ningún artefacto legacy ni escribir ningún extractor nuevo — para dimensionar con evidencia real el esfuerzo de cerrar el gap de UI/navegación/reportes antes de comprometerse a construirlo.

**Quedo a la espera de tu revisión y aprobación antes de continuar con cualquier implementación.**
