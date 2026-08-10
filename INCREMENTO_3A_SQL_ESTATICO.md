# Incremento Funcional 3A — Resolución de SQL Estático y Multilínea

**Fecha**: 2026-08-06
**Alcance**: reconstruir, DENTRO DEL MISMO MÉTODO, el SQL referenciado por variable o `StringBuilder` cuando la construcción es lineal (literal, multilínea, verbatim, concatenación `+`, `StringBuilder.Append/AppendLine/ToString`). Sin tocar Evidence, Confidence, esquema SQLite, app.config ni Settings.cs — todo eso ya validado en Incrementos 1-2. Sin análisis entre métodos, Reflection, Stored Procedures, ejecución simbólica.

---

## 1. Validación previa — clasificación real de los 522 grupos sin resolver

Antes de escribir una sola línea de resolución nueva, se clasificó por causa raíz, contra el código decompilado real, cada uno de los 522 grupos (clase, método) que hoy solo muestran el mensaje genérico. El script de clasificación pasó por 3 rondas de auto-corrección al encontrarse bugs reales en el propio diagnóstico (documentados en el script, `reanalysis_2026-08-06_incremento3a/unresolved_classification.json`):

| Causa raíz | Grupos | % | ¿Este incremento la resuelve? |
|---|---|---|---|
| Concatenación simple (`+`) en una sola sentencia | 345 | 66.1% | **Sí** |
| Literal puro, bloqueado solo por falta de detección de la variable | 131 | 25.1% | **Sí** |
| Variable/StringBuilder con ramificación (if/else/ternario) | 39 | 7.5% | No — alcance explícito |
| Otros (trigger no localizado, StringBuilder sin declaración en ventana) | 7 | 1.3% | No — residual |

**Hallazgo clave, no anticipado en el pedido original**: el cuello de botella real NO era "falta de soporte a StringBuilder/concatenación" en abstracto — era que **ningún regex de detección de variable en `extract.py` reconocía el patrón `new SqlCommand(cmdText, connection)` (dos argumentos)**, el patrón dominante real del portafolio (confirmado en `AFL.Dashboard`, `AFLProdMon`, `DataTransfer`, etc.). Sin reconocer siquiera el NOMBRE de la variable, nunca se llegaba a intentar resolverla — ni con el mecanismo viejo ni con uno nuevo. Corregir esa detección (una función, sin tocar Evidence/Confidence/esquema) desbloqueó, por sí sola, el 25.1% marcado como "literal puro" arriba. Este hallazgo se documenta explícitamente en el código (`analyzer/extract.py`) y en los tests, no se ocultó.

Con esto confirmado: **el grupo más numeroso (concatenación simple) SÍ es el que este incremento resuelve**, exactamente como pedía la validación previa.

## 2. Archivos modificados

| Archivo | Cambio |
|---|---|
| `analyzer/extract.py` | Nuevas funciones: `_skip_string_literal`, `_unescape_csharp_literal`, `_find_statement_end`, `_find_matching_close_paren`, `_tokenize_string_expression`, `_render_tokens`, `_reconstruct_dynamic_sql` (reemplaza `_resolve_variable`/`STRING_VAR_ASSIGN`, que quedaban subsumidos). Detección de variable extendida (`VAR_IN_COMMANDTEXT_ASSIGN`, `VAR_AS_COMMAND_CTOR_ARG`, `TOSTRING_IN_TRIGGER`). Ver sección 1 para el porqué. |
| `analyzer/db.py` | El INSERT de `sql_findings` ahora persiste las columnas de Evidence (ya existían desde el Incremento 1, nadie las llenaba para SQL). Sin cambio de esquema. |
| `analyzer/report.py` | `_rows_for_method` ahora también entrega `evidence`; la tabla de "Funciones -> SQL" muestra columnas **Evidencia**/**Confianza**. Bug real corregido: la señal "¿este row tiene contenido real?" era `'"' in text` — funcionaba por accidente porque el texto resuelto SIEMPRE conservaba las comillas de C#; la reconstrucción nueva entrega SQL ya limpio (sin comillas envolventes) y ese heurístico fallaba para queries 100% literales sin ninguna comilla embebida (ej. `SELECT JobId, PartNo FROM DJItem WHERE Active = 1`). Corregido a la señal real: `f.resolved is not None or '"' in f.raw`. |
| `analyzer/export_office.py` | Mismas columnas Evidencia/Confianza en el sheet Excel y la tabla Word de SQL. |
| `tests/test_characterization.py` | `TestDataTransferStringBuilderGap` actualizado a propósito (invitación explícita de su propio docstring): el caso StringBuilder lineal de `DataTransfer` ahora SÍ resuelve. `TestSgiStringBuilderGap` **sin cambios** — su caso tiene ramificación if/else, queda sin resolver correctamente. |
| `tests/fixtures/concat_case/`, `tests/fixtures/ternary_branch_case/` | Nuevos, recortados de código real (`AFL.Dashboard/Class1.cs`). |
| `tests/fixtures/verbatim_multiline_case/` | Nuevo, sintético (no se encontró una instancia real aislada de verbatim-multilínea-por-variable para citar; mismo criterio ya usado en `happy_path`/`dedup_case`). |
| `tests/test_increment3a_sql_reconstruction.py` | **Nuevo**, 12 tests. |

## 3. Bugs reales encontrados y corregidos durante la implementación

1. **Tokenizador de expresiones tragaba el prefijo `@`/`$` de un literal verbatim/interpolado** cuando venía inmediatamente después de otro segmento — `@"..."` se partía en `expr="@"` + `literal="..."` en vez de un solo literal. Corregido en `_tokenize_string_expression` (el chunk "expr" ahora se detiene si lo que sigue ya es el inicio de un literal, no solo ante `"`/`+`). Detectado con el fixture `verbatim_multiline_case`.
2. **`report.py`'s heurístico `'"' in text`** para decidir "¿hay contenido real?" — ver tabla de arriba. Detectado con el fixture `happy_path` (regresión real, test ya existente empezó a fallar).
3. (Herramienta de validación, no producto) El script de clasificación de la sección 1 pasó por 2 rondas de auto-corrección: primero clasificaba por la fila `SqlConnection` del grupo en vez de la `SqlCommand`/`CommandText` real; después localizaba mal la línea del trigger por buscar la primera palabra ("using", ambigua) en vez de contar ocurrencias de `SQL_TRIGGER`.

## 4. Resultado de pytest

```
76 passed
```

(64 de los Incrementos 1-2 + 12 nuevos de este incremento. Cero tests existentes rotos sin una razón real y explícitamente documentada — ver sección 2.)

## 5. Validación funcional en vivo

App Flask real, `AFL.Dashboard/AFL.Dashboard` reanalizada: el método `UpdateJobLinea` ahora muestra en el reporte real (`/apps/<id>`) —

```
SQL / Query: Update LCJob set Linea='{linea}',EntregadoA='{entregadoA}'  where ID={idJob}
Evidencia: PARTIAL_RECONSTRUCTION, linea 410     Confianza: 80%
```

en vez del mensaje genérico. Confirmado también vía `test_client()` contra la ruta real, y vía export Excel/Word (ambos con las columnas Extractor/Linea/Confianza nuevas).

## 6. Reanálisis del portafolio — Antes vs Después

Mismo mecanismo ya usado en el Incremento 2: 100% local contra `decompiled/` (red no disponible), backup tomado antes (`qapv_analyzer.before_increment3a_reanalysis.db`), `db_procedures`/`db_tables`/`companion_assemblies`/`review_status` preservados explícitamente across el upsert. **61/61 apps reanalizadas, 0 fallidas.**

| Métrica | Antes | Después |
|---|---|---|
| Grupos (clase, método) con hallazgo SQL | 1,201 | 1,201 |
| **Grupos que SOLO muestran el mensaje genérico** | **522 (43.5%)** | **87 (7.2%)** — reducción del 83.3% |
| `sql_findings` categoría `query` resueltas | 79 (2.5%) | **723 (22.9%)** — +644 queries nuevas |
| `db_procedures` / `db_tables` (introspección real, preservados) | 87 / 301 | 87 / 301 |
| Total de filas en cada tabla (`apps`/`settings`/`sql_findings`/`io_findings`/`security_flags`/`findings`) | sin cambio | sin cambio (61 / 382 / 3,477 / 710 / 590 / 206) |

### Distribución de confianza (sql_findings, categoría `query`)

| Rango | Antes | Después |
|---|---|---|
| 80–90 (HARDCODED_METHOD_LITERAL=90 + PARTIAL_RECONSTRUCTION=80) | 0 | **723** |
| Sin evidencia (UNKNOWN) | 3,163 | 2,440 |

Por extractor: `HARDCODED_METHOD_LITERAL` (100% literal, sin ninguna variable dinámica) = 232; `PARTIAL_RECONSTRUCTION` (esqueleto literal conocido + segmentos dinámicos marcados entre `{llaves}`, nunca inventados) = 491.

### Aplicaciones más beneficiadas (queries nuevas resueltas)

| App | Queries nuevas resueltas |
|---|---|
| DataTransfer | 111 |
| VINS1/VINS1 | 72 |
| AFLProdMon/AFLProdMon | 49 |
| EndFiberClean/EndFiberClean | 38 |
| FaceLabUnion/FaceLabUnion | 34 |
| INVENTA2-2TEST/InventaVales - rebuild | 27 |
| OTDR/OTDR | 24 |
| AFL.Dashboard/AFL.Scrap | 24 |
| AFL.Dashboard/AFL.ReporteImpresiones | 24 |
| AFL.Dashboard/AFL.RegistroOperacion | 24 |

### Ejemplos reales de SQL ahora reconstruido

**`DataTransfer.btnImprimir_Click` → `dbo.TWaveLength`** (PARTIAL_RECONSTRUCTION, 80%):
```
Select space(50) as SerialNumber, '{text2}' as PartNumber, '{text4}' as Description,
'{text}' as SalesOrder, dbo.TDUTMeasurement.Dum_Id, Dum_MeasurementDate as Fecha, ...
```

**`EndFiberClean.Repository.ObtenerHorasDowntime` → `EndFiberClean_CountTime`** (HARDCODED_METHOD_LITERAL, 90% — SQL con CTE completo, 100% literal, sin ninguna variable):
```sql
-- Genera particiones por hora dentro de cada evento
WITH EventosOriginales AS (
    SELECT ID, ...
```

## 7. Detenido aquí, como se pidió

No se empezó ni variables globales ni análisis entre métodos ni ningún otro extractor. El 7.2% restante de grupos genéricos (87) corresponde casi en su totalidad a ramificación real (if/else/ternario) — alcance explícitamente fuera de este incremento.
