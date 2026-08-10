# Plan de Implementación — Validation Framework

Cada fase es pequeña, independiente y de bajo riesgo por diseño: ninguna fase reescribe una fase anterior, cada una tiene su propio criterio de salida verificable (tests en verde), y el orden respeta las dependencias reales identificadas en `ARCHITECTURE_REVIEW.md` (no se puede capturar `resolution_status` antes de que exista el campo; no se puede calcular Coverage antes de que `resolution_status` exista con datos reales).

**Regla de aceptación transversal a todas las fases (no negociable)**: ninguna fase se da por cerrada sin que su(s) caso(s) de prueba correspondiente(s) de `TEST_STRATEGY.md` estén en verde. Si una fase introduce un cambio de comportamiento en `extract.py`/`enrich.py`, el fixture real del portafolio afectado (`ReportViewer`, `InterConfig`, `InterAFL`, `SGI`, `DataTransfer`, `AlmacenDiagnostico`) debe validarse contra el snapshot dorado antes de mover al portafolio completo.

---

## Fase 0 — Infraestructura de test (prerequisito de TODO lo demás)

**Objetivo**: que exista una red de seguridad antes de tocar `extract.py`/`enrich.py`.

**Alcance**:
- Agregar `pytest` a `requirements.txt`.
- Crear `tests/`, `tests/fixtures/`, `tests/conftest.py`.
- Copiar (congelar) los 6 fixtures reales ya identificados desde `decompiled/` hacia `tests/fixtures/` (ver `TEST_STRATEGY.md` para la lista exacta de archivos).
- Escribir **tests de caracterización** del comportamiento ACTUAL (antes de cualquier cambio) — no prueban que el comportamiento sea correcto, prueban que es el que ya conocemos, para detectar cualquier regresión accidental de las fases siguientes sobre código que hoy funciona bien.

**Riesgo**: mínimo — no se modifica ningún módulo de producción, solo se agrega infraestructura de test y fixtures congelados.

**Criterio de salida**: `pytest` corre localmente, los tests de caracterización pasan contra el código actual sin modificar.

**Bloquea**: todas las fases siguientes.

---

## Fase 1 — Plomería: `Evidence`, `Confidence`, catálogo de causas (sin cambiar comportamiento de extracción todavía)

**Objetivo**: construir los módulos nuevos y las columnas de esquema que el resto de fases van a usar, sin todavía conectarlos a la lógica real de `extract.py` — esta fase es deliberadamente "código nuevo que no se usa aún" para poder probarlo de forma aislada antes de integrarlo.

**Alcance**:
- `analyzer/__version__.py` (constante `ANALYZER_VERSION`).
- `analyzer/evidence.py` (dataclass `Evidence`, ver `VALIDATION_FRAMEWORK.md` sección 0).
- `analyzer/confidence.py` (`CONFIDENCE_TABLE` + función de resolución).
- `analyzer/failure_catalog.py` (`FAILURE_CATALOG` + `code_from_sqlstate()`).
- Migración de esquema (`db.py: init_db()`, patrón `ALTER TABLE` ya establecido): agregar `line`, `extractor`, `pattern`, `confidence`, `analyzer_version`, `resolution_status` a `sql_findings`/`settings`; tabla nueva `unknowns` (DDL completo en `VALIDATION_FRAMEWORK.md` sección 4.1).
- Punto único de conversión dataclass↔fila de BD (resuelve L24 de `KNOWN_LIMITATIONS.md`) — refactor interno de `db.py`/`report.py`, sin cambiar ningún dato ni comportamiento observable.

**Riesgo**: bajo — son módulos nuevos sin consumidores todavía, más una migración de esquema aditiva (columnas nuevas con default NULL no rompen ninguna fila existente, mismo patrón ya usado ~6 veces en el historial de `init_db()`).

**Criterio de salida**: tests unitarios de `confidence.py`/`failure_catalog.py`/`evidence.py` en verde; re-ejecutar el pipeline completo sobre 1 app conocida (ej. `AFL.Dashboard`) y confirmar que el reporte generado es **byte-idéntico** al de antes de la migración (las columnas nuevas existen pero nada las llena todavía).

**Depende de**: Fase 0.

---

## Fase 2 — Conectar `resolution_status` + Failure Reason Catalog a los gaps ya conocidos (L1, L9, L21, L22)

**Objetivo**: el primer cambio de comportamiento real, acotado a los 4 gaps P0 ya identificados y ya cubiertos por fixtures.

**Alcance**:
- `extract.py`: `_resolve_variable()` gana un segundo modo de búsqueda a nivel de campo de clase (resuelve L1 — caso `AlmacenDiagnostico`); `_classify_sql()`/`scan_file()` retornan explícitamente `resolution_status` en vez de dejar `target=None` en silencio (resuelve L9).
- `enrich.py`: `_short_error()` reemplazado por `failure_catalog.code_from_sqlstate()` (resuelve L21).
- `report.py`: el mensaje genérico de la línea 50 se reemplaza por el mensaje de plantilla del `reason_code` ya capturado (resuelve L22) — **este archivo deja de decidir causas, solo las formatea**, coherente con la separación de responsabilidades de `ARCHITECTURE_REVIEW.md` sección 6.

**Riesgo**: medio — toca la función más veces corregida del historial del proyecto (`_classify_sql`). Mitigado por: (a) Fase 0 ya en verde antes de empezar, (b) fixtures de `AlmacenDiagnostico` (L1) y de cualquier conexión rota conocida (L21) como snapshot dorado exacto, (c) cambio acotado a 3 archivos, sin tocar `db.py`'s SCHEMA otra vez (ya migrado en Fase 1).

**Criterio de salida**: los 2 fixtures nuevos de esta fase (`AlmacenDiagnostico` connection string de clase; un mock de `pyodbc` con 3 tipos de excepción distintos) pasan con mensajes específicos, no genéricos. Los fixtures de caracterización de Fase 0 siguen en verde (no se rompió nada de lo que ya funcionaba).

**Depende de**: Fase 1.

---

## Fase 3 — SQL armado con `StringBuilder` (L8)

**Objetivo**: cerrar el gap de mayor impacto confirmado (afecta a las 2 apps más grandes del portafolio), aislado en su propia fase por ser el cambio de extracción más complejo de todos.

**Alcance**: `extract.py` gana seguimiento de objetos `StringBuilder` (detectar `new StringBuilder()`, acumular `.Append()`/`.AppendLine()` hasta el `.ToString()` que alimenta un `CommandText =`), con `resolution_status='resolved'` cuando se logra reconstruir el texto completo, o `'unresolved_dynamic_sql'` con `reason_code=DYNAMIC_SQL` cuando no (ej. contenido condicional que no se puede resolver estáticamente sin ejecutar la app).

**Riesgo**: medio-alto — es la extensión de extracción más nueva conceptualmente (nunca se ha trackeado un objeto mutable a través de líneas, solo asignaciones directas). Mitigado por los 2 fixtures ya identificados y verificados a mano (`SGI/SurtirVM.cs:429-439`, `DataTransfer.cs:14479`).

**Criterio de salida**: ambos fixtures resuelven `target` conteniendo el nombre de tabla correcto (`ValeRH`/`ValePartes`/`ValesHistorico` para SGI; `XXAFL_QAPV_REWORKS_PRUEBA`/`Reworks_PRUEBA` para DataTransfer), no `None`.

**Depende de**: Fase 2 (reutiliza el mecanismo de `resolution_status`/`reason_code` ya wireado).

---

## Fase 4 — Reflection, COM/CLSID, Modbus/PLC (L16, L17, L18)

**Objetivo**: nueva categoría de finding para invocación indirecta/tardía, y una integración física adicional ya confirmada.

**Alcance**:
- Nueva categoría de patrón en `extract.py` (o módulo hermano `reflection_extract.py` si `extract.py` empieza a sentirse sobrecargado — decisión a tomar en el momento, no de antemano) para: `Activator.CreateInstance`, `MethodInfo.Invoke`/`GetMethod().Invoke`, `Marshal.GetTypeFromCLSID`, `dynamic` operando un objeto obtenido de una de las llamadas anteriores.
- `LOCAL_IO_TRIGGER` gana `ModbusClient`/`EasyModbus` como patrón de integración PLC.
- Tabla `reflection_findings` (o reutilizar `io_findings` con una categoría nueva — decisión de diseño a confirmar contra el volumen real antes de crear una tabla nueva; ver nota de riesgo abajo).

**Riesgo**: medio — patrón nuevo, pero acotado a 7 apps ya identificadas con fixtures conocidos (`DataTransfer`/`VINS1` para reflection y COM, `VINS1/Modbus` para PLC).

**Criterio de salida**: los 3 fixtures nuevos (`PrintReportViewer.cs`, `MainVM.cs:1178` de ReportViewer, `Modbus/Form1.cs:41-42`) generan findings de la categoría correspondiente.

**Depende de**: Fase 1 (usa `Evidence`/`confidence`), independiente de Fase 3.

---

## Fase 5 — Discovery Coverage + Resolution Coverage (Read Models)

**Objetivo**: ahora que `resolution_status`/`Evidence`/`confidence` existen con datos reales (Fases 1-4 ya poblaron estos campos para los gaps conocidos), calcular las métricas de cobertura tiene sentido — calcularlas antes habría producido un número artificialmente alto sobre una base incompleta (riesgo ya señalado en `VALIDATION_FRAMEWORK.md` sección 5 de la priorización).

**Alcance**: `db.py`: `get_discovery_coverage(app_id)`, `get_resolution_coverage(app_id)`, siguiendo el registro declarativo `ARTIFACT_TYPES` de `VALIDATION_FRAMEWORK.md` sección 1.1. Nueva vista en `/apps/<id>` o pestaña dedicada.

**Riesgo**: bajo — es agregación de solo lectura sobre datos que ya existen tras las fases anteriores, mismo patrón que el Priority Engine (ya probado en producción).

**Criterio de salida**: para los 6 apps fixture, el Coverage calculado coincide con el conteo manual ya verificado en esta sesión (ej. `ReportViewer`: 1 conexión, 3 SPs resueltos de 3 — 100% Resolution Coverage para SPs).

**Depende de**: Fases 1-4 (necesita datos reales poblados).

---

## Fase 6 — Unknowns Engine (tabla + generación automática + UI)

**Objetivo**: poblar `unknowns` automáticamente desde `resolution_status`/`connection_errors` ya capturados (no requiere lógica nueva de detección, solo de traducción a filas de `unknowns` vía el catálogo de causas).

**Alcance**: `db.py`: función que, en el momento de `save_analysis()`/`save_db_objects()`, inserta una fila en `unknowns` por cada finding con `resolution_status` distinto de `resolved`/`not_applicable`, interpolando la plantilla del `reason_code` correspondiente. Nueva sección "Unknowns" en el reporte de cada app (`report.py`/`templates/`).

**Riesgo**: bajo — es la consecuencia mecánica de las fases anteriores, no introduce detección nueva.

**Criterio de salida**: para los 6 fixtures, la lista de `Unknowns` generada coincide exactamente con los gaps ya documentados a mano en `KNOWN_LIMITATIONS.md` para esa app.

**Depende de**: Fases 1-5.

---

## Fase 7 — Reprocesar el portafolio completo (backfill)

**Objetivo**: aplicar todo lo anterior a las ~60+ apps ya analizadas, igual que se hizo para el fix de `app.config` — re-extracción desde `decompiled/` (sin re-decompilar), preservando `review_status`/`review_notes`/`findings` (ya garantizado por el upsert-by-name existente).

**Alcance**: reutilizar el script de re-escaneo ya usado y validado en esta misma sesión (`rescan_all.py`), extendido para poblar los campos nuevos; re-ejecutar `enrich_app()` para todo el portafolio después (mismo patrón, ya que el re-guardado borra en cascada `db_procedures`/`db_tables`).

**Riesgo**: medio (por volumen, no por lógica) — ya se demostró que este patrón funciona sin pérdida de datos curados; el riesgo real es tiempo de ejecución (decenas de conexiones reales a servidores de planta), no corrección.

**Criterio de salida**: `review_status`/`review_notes`/`findings` de las 8+8+8 apps ya revisadas en sesiones anteriores permanecen intactos; Coverage/Confidence/Unknowns aparecen pobladas para el 100% del portafolio.

**Depende de**: Fases 1-6.

---

## Fase 8 (opcional, menor prioridad) — Pulido: Views/Functions, SP-not-found vs. sin-permiso (L10, L14)

**Objetivo**: cerrar las 2 limitaciones P2 restantes que no requieren cambios de extracción de C#, solo de introspección de BD ya existente.

**Alcance**: `db_introspect.py` gana una consulta a `sys.objects.type` para distinguir vista/función/tabla y una consulta separada de existencia (`sys.objects`) antes de intentar `OBJECT_DEFINITION()`.

**Riesgo**: bajo — extensión acotada de un módulo ya estrictamente de solo lectura, sin tocar `extract.py`.

**Depende de**: ninguna de las anteriores estrictamente — puede intercalarse en cualquier momento después de la Fase 0, se deja al final por prioridad (P2), no por dependencia técnica.

---

## Resumen de dependencias (para planear el orden real de trabajo)

```
Fase 0 (tests) ──> Fase 1 (plomeria) ──> Fase 2 (gaps P0) ──┬──> Fase 3 (StringBuilder)
                                                              ├──> Fase 4 (Reflection/COM/Modbus)
                                                              └──> Fase 8 (pulido, opcional, sin bloqueo)
                                                                        │
                                          Fase 3 + Fase 4 ──> Fase 5 (Coverage) ──> Fase 6 (Unknowns) ──> Fase 7 (backfill portafolio)
```

Fases 3 y 4 pueden trabajarse en paralelo (no se tocan entre sí). Fase 8 puede intercalarse en cualquier punto después de Fase 0. Ninguna fase después de la 2 requiere volver a tocar `report.py`'s mensajes genéricos — ya quedan resueltos ahí.

## Qué NO se planea implementar en estas fases (alcance explícitamente excluido)

- Unificación de `findings`/`security_flags`/`unknowns` en una sola tabla con discriminador (deuda reconocida en `ARCHITECTURE_REVIEW.md` sección 7, deliberadamente diferida).
- El grafo de conocimiento en sí (sección 7 de `VALIDATION_FRAMEWORK.md` es solo preparación de datos, no un motor de grafo).
- División de `db.py` en un paquete por dominio (documentado como decisión pendiente, no urgente).
- Cualquiera de los patrones "sentinela" de `KNOWN_LIMITATIONS.md` (P3) sin evidencia nueva de que aparecen en una app real.
