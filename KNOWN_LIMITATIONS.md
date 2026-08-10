# Limitaciones Conocidas — QAPV Legacy App Analyzer

Ninguna limitación de esta herramienta debe quedar implícita. Cada fila de este documento es una limitación real, confirmada con evidencia (ver `VALIDATION_STRATEGY.md` para el detalle de auditoría y `ARCHITECTURE_REVIEW.md` para el análisis arquitectónico), no una suposición. Se actualiza cada vez que una limitación se cierra (mover a "Resuelta") o se descubre una nueva.

**Convención de `Estado`**: `Activa` (existe hoy, no se ha trabajado) / `Planeada` (tiene fase asignada en `IMPLEMENTATION_PLAN.md`) / `Mitigada` (existe un workaround pero no está resuelta de raíz) / `Resuelta` (cerrada, se deja el registro histórico).

---

## Extracción de conexiones

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L1 | Connection strings declaradas como campo de clase (fuera de cualquier método) son invisibles — `_resolve_variable()` solo escanea desde el inicio del método actual | Planeada | Alto — confirmado 1 caso real con credencial de producción en texto plano (`AlmacenDiagnostico/Program.cs:230`) | P0 | `VALIDATION_FRAMEWORK.md` sección 2 (Resolution Coverage) + extender el escaneo de `find_settings`/`_resolve_variable` a nivel de clase | v0.6.1 |
| L2 | OleDb con connection string concatenada en tiempo de ejecución (no declarada estática) no se captura | Activa | Medio — 1 app confirmada (`InterAFL`, importación de Excel) | P2 | Extender `SQL_TRIGGER`/`find_settings` con un patrón para `new OleDbConnection(` + resolución de la concatenación | v0.6.3 |
| L3 | Alias TNS de Oracle en formato simple (`Data Source=Alias;User Id=x;Password=y`, sin el descriptor completo `(DESCRIPTION=...)`) se clasificarían incorrectamente como SQL Server por `enrich.py`'s `ORACLE_HINT` | Activa (defecto de diseño, no confirmado en el portafolio actual) | Bajo hoy / Alto si aparece — produciría un intento de conexión con el driver equivocado y un error de conexión engañoso | P3 | Ampliar `ORACLE_HINT` para reconocer nombres de alias TNS conocidos, o exigir una lista explícita de qué connection strings son Oracle en vez de inferirlo | Sin asignar — solo si se confirma un caso real |
| L4 | `<appSettings>` de `app.config` (distinto de `<connectionStrings>`) nunca se parsea | Activa | Bajo — no se confirmó que oculte credenciales, pero el mecanismo no existe | P3 | Extender `_find_appconfig_connection_strings` para incluir `<appSettings>` con el mismo filtro `LOOKS_LIKE_DB_CONN` | Sin asignar |
| L5 | `web.config`/`user.config` no soportados | Activa (no aplica hoy) | Ninguno confirmado — el portafolio actual son solo ejecutables de escritorio | P3 (sentinela, no implementar) | Caso de prueba centinela en `TEST_STRATEGY.md` que alerte si aparece un `web.config` real en una app futura | N/A hasta que se confirme necesidad |
| L6 | Variables de entorno, Registro de Windows, INI, XML propio, JSON como fuente de credenciales de BD | Activa (no aplica hoy, confirmado por auditoría exhaustiva) | Ninguno confirmado en las 60 apps ya analizadas | P3 (sentinela) | Igual que L5 — caso centinela, no construir soporte sin evidencia | N/A |
| L7 | ODBC DSN / `OdbcConnection` | Activa (no aplica hoy) | Ninguno confirmado | P3 (sentinela) | Igual que L5 | N/A |

## Extracción de SQL

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L8 | SQL armado con `StringBuilder`/`.Append()` no se resuelve — el finding se guarda con `target=None` sin ninguna indicación de la causa | Planeada | **Alto** — confirmado en las 2 apps más grandes del portafolio (`DataTransfer`, `SGI`), afecta directamente al Data Dictionary y Dependency Graph del portafolio (subestiman tablas reales) | P1 | `VALIDATION_FRAMEWORK.md` secciones 2 y 5 — nuevo `resolution_status='unresolved_dynamic_sql'` + tracking de objetos `StringBuilder` en `extract.py` | v0.6.2 |
| L9 | `sql_findings.target IS NULL` conflacta "resuelto, genuinamente sin tabla" con "no se pudo resolver" | Planeada | Alto — ambigüedad estructural, afecta la confiabilidad de toda métrica que use `target` | P0 | Campo `resolution_status` explícito (sección 2.1 del framework) | v0.6.1 |
| L10 | Vistas (Views) y Funciones con valor de tabla (TVF) no se distinguen de tablas regulares en `_classify_sql()` | Activa | Medio — el Diccionario de Datos del portafolio puede mostrar una vista como si fuera una tabla base | P2 | Usar `sys.objects.type` ya disponible vía `db_introspect.py` cuando la introspección real tiene éxito; heurística de nombre como fallback cuando no hay introspección | v0.6.4 |
| L11 | Comando SQL creado en un método/archivo distinto de donde se ejecuta (inyección de dependencias, helper compartido) no se sigue | Activa (no confirmado en el portafolio actual — todo el código es de un solo método) | Bajo hoy | P3 (sentinela) | Caso centinela — si aparece, requeriría análisis cross-archivo, cambio de diseño mayor | N/A hasta que se confirme necesidad |
| L12 | Entity Framework, Dapper, LINQ-to-SQL, interpolación de string (`$"..."`) para SQL | Activa (no aplica hoy, confirmado por auditoría exhaustiva) | Ninguno confirmado | P3 (sentinela) | Caso centinela | N/A |
| L13 | TableAdapter / DataSet Designer (`.xsd`) | Activa (no aplica hoy) | Ninguno confirmado | P3 (sentinela) | Caso centinela | N/A |

## Stored Procedures

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L14 | `OBJECT_DEFINITION()` retorna `NULL` tanto si el SP no existe como si el login no tiene permiso `VIEW DEFINITION` — no se distinguen | Activa | Medio — un reporte puede decir "SP no encontrado" cuando en realidad es un problema de permisos, llevando a la conclusión equivocada (¿el código está mal? ¿o el usuario de introspección necesita más permisos?) | P2 | Consulta adicional contra `sys.objects` (existencia) separada de `OBJECT_DEFINITION()` (contenido), para reportar `SP_NOT_FOUND` vs. `PERMISSION_DENIED` como causas distintas del catálogo (`VALIDATION_FRAMEWORK.md` sección 5) | v0.6.2 |
| L15 | Result set no determinable estáticamente (`sys.dm_exec_describe_first_result_set_for_object` retorna vacío) no distingue "SQL dinámico dentro del SP" de "múltiples result sets" de "usa tabla temporal" | Activa | Bajo — ya se informa como "no determinable", solo falta granularidad de causa | P3 | Mensaje más específico basado en un análisis simple del texto de la definición ya obtenida (buscar `EXEC(`, `#temp`, múltiples `SELECT` de nivel superior) | v0.6.4 |

## Confiabilidad del análisis estático (Reflection / COM / integraciones)

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L16 | Cero detección de Reflection (`Assembly.Load`, `Activator.CreateInstance`, `Type.GetType`, `MethodInfo.Invoke`, `dynamic` para late-binding) | Planeada | **Alto** — confirmado en 2 apps (`DataTransfer`, `VINS1`) invocando miembros NO PÚBLICOS de un control de terceros; el comportamiento real de esa app depende de una API no documentada de una versión específica de una librería | P1 | Nueva categoría de finding "reflection", ver `VALIDATION_FRAMEWORK.md` componente Discovery Coverage (`reflection`) | v0.6.3 |
| L17 | Cero detección de COM/ActiveX vía CLSID (`Marshal.GetTypeFromCLSID` + `Activator.CreateInstance`) — evade cualquier búsqueda de referencia estática `Microsoft.Office.Interop.*` | Planeada | Alto — 5 apps confirmadas dependen de Excel instalado en el host en tiempo de ejecución | P1 | Mismo mecanismo que L16 (comparten primitivas de detección) | v0.6.3 |
| L18 | Segunda instancia de integración PLC/Modbus (`VINS1/Modbus`) no detectada como integración — solo se documentó a mano en `MonTemp2` | Planeada | Medio — riesgo real de migración: perder silenciosamente un canal de comunicación con equipo físico de planta | P2 | Agregar patrón `ModbusClient`/`EasyModbus` a `LOCAL_IO_TRIGGER` o registro equivalente | v0.6.3 |
| L19 | El nombre del ejecutable no predice su funcionalidad real (confirmado: `ReportViewer.exe` no usa el control ReportViewer; lo usan `DataTransfer`/`VINS1`) | Resuelta (como hallazgo, no como "bug" — es una regla operativa, no un defecto de código) | Medio — riesgo de asumir cobertura por nombre en vez de verificar contra el código | N/A | Ya documentado como principio operativo en `VALIDATION_STRATEGY.md` y `ARCHITECTURE_REVIEW.md` — ningún cambio de código requerido, es una disciplina de auditoría | — |
| L20 | Sin soporte para FTP, MSMQ, RabbitMQ, Named Pipes (a nivel app), Sockets crudos (a nivel app), OPC, Crystal Reports, Windows Services, SOAP/WCF | Activa (no aplica hoy, confirmado por auditoría exhaustiva) | Ninguno confirmado en las 60 apps ya analizadas | P3 (sentinela) | Casos centinela en `TEST_STRATEGY.md` | N/A hasta que se confirme necesidad en una app nueva |

## Estado de conexiones y diagnóstico

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L21 | Mensajes de error de conexión ambiguos — todo fallo que no sea un servidor de la lista curada (`naamrt-qcs11`) colapsa en 1-2 mensajes genéricos, sin distinguir DNS/timeout/auth/permisos | Planeada | **Alto** — motivo original de esta fase del proyecto | P0 | Failure Reason Catalog (`VALIDATION_FRAMEWORK.md` sección 5), mapeo de SQLSTATE a causa estructurada | v0.6.1 |
| L22 | Mensaje genérico "query no resuelta automáticamente" no distingue ninguna causa (`report.py:50`) | Planeada | Alto — mismo grupo que L21/L8/L1, es el síntoma visible de todos ellos | P0 | Reemplazar por el `reason_code` ya capturado en el momento de la extracción (secciones 2 y 5 del framework) — nunca inventar el diagnóstico en `report.py` | v0.6.1 |

## Deuda arquitectónica (no son "bugs", son decisiones a revisar)

| # | Limitación | Estado | Impacto | Prioridad | Plan de mitigación | Versión objetivo |
|---|---|---|---|---|---|---|
| L23 | Ningún dato tiene campo de confianza o evidencia estructurada (extractor responsable, línea exacta, versión del analizador) | Planeada | Alto — precondición de todo el Validation Framework | P0 | `Evidence` value object (`VALIDATION_FRAMEWORK.md` sección 0) | v0.6.1 |
| L24 | `SqlFinding`/`SettingEntry`/`LocalIOFinding` se serializan a mano en 3 lugares distintos (`extract.py`, `db.py: save_analysis`, `report.py: reconstruct_from_db`), sin un punto único de conversión | Activa | Medio — riesgo de desincronización silenciosa al agregar campos nuevos (ya ocurrió antes en el historial del proyecto con un bug de clasificación de SPs) | P1 | Ver `ARCHITECTURE_REVIEW.md` sección 2 y 6 — introducir un mapeo centralizado dataclass↔fila antes de agregar más campos | v0.6.1 (junto con L23, mismo cambio) |
| L25 | Tres tablas ya representan "algo observado sobre una app" con formas distintas (`security_flags`, `findings`, `sql_findings`/`io_findings`) sin discriminador de tipo unificado — ya diagnosticado en `VISION.md` sección 11, no resuelto | Activa (deuda heredada, no de esta fase) | Medio — se agrava con cada tabla nueva que no siga el mismo vocabulario | P2 (mitigado, no bloqueante) | Las tablas nuevas (`unknowns`) se diseñan compatibles con el vocabulario existente (`FINDING_SEVERITIES`) para no agravarlo más, sin migrar las 3 tablas existentes en esta fase | Sin asignar (unificación real es una decisión de v1.0+) |
| L26 | Cero tests automatizados en todo el proyecto pese a ~15 bugs reales ya corregidos en el extractor a lo largo de su historia | **Resuelta** (2026-08-06) — `tests/` con pytest, 7 fixtures reales congelados, 13 tests de caracterización + centinela, todos en verde | Alto (ya mitigado) — bloqueaba con seguridad cualquier cambio a `extract.py`/`enrich.py`, ya no | P0 | `TEST_STRATEGY.md` — infraestructura de pytest + fixtures reales del portafolio, Fase 0 de `IMPLEMENTATION_PLAN.md` | v0.6.0 — cerrada |
| L27 | `analyzer/db.py` crece con cada capacidad nueva de portafolio (810+ líneas), sin convención decidida para dividirlo (archivo único vs. paquete `db/` por dominio) — ya señalado en la autoevaluación de VISION.md v0.5 | Activa | Bajo hoy, crece con el tiempo | P3 (documentar, no ejecutar aún) | Decidir la convención cuando el archivo supere un umbral acordado (ej. 1500 líneas) o cuando se agregue la 3ra capacidad de portafolio nueva, lo que ocurra primero | Sin asignar |

---

## Resumen por severidad

- **P0 (bloqueante, se resuelve antes que cualquier otra cosa)**: L1, L9, L21, L22, L23, L24, L26 — 7 limitaciones, todas dentro de la Fase 0-1 de `IMPLEMENTATION_PLAN.md`.
- **P1 (alto valor, siguiente en la fila)**: L8, L16, L17 — 3 limitaciones con evidencia de impacto real confirmado en apps ya analizadas.
- **P2 (valor medio, después de P0/P1)**: L2, L10, L14, L18, L25.
- **P3 (documentado, no se implementa sin nueva evidencia)**: L3, L4, L5, L6, L7, L11, L12, L13, L15, L20, L27 — coherente con el Principio 4 de `ARCHITECTURAL_PRINCIPLES.md` (la complejidad solo crece con evidencia objetiva de que el valor supera el costo).

Este documento se actualiza en cada fase de `IMPLEMENTATION_PLAN.md` — ninguna limitación pasa a "Resuelta" sin su caso de prueba correspondiente en `TEST_STRATEGY.md` primero.
