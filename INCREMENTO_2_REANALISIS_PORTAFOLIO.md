# Incremento Funcional 2 — Instrumentación de Settings.cs + Reanálisis del Portafolio

**Fecha**: 2026-08-06
**Alcance**: Instrumentar `find_settings()` (Settings.cs / `DefaultSettingValue`) con Evidence real, aplicando exactamente el patrón ya validado en el Incremento 1 (app.config). Reanálisis completo del portafolio con el analizador resultante. Ningún otro extractor (SQL dinámico, Reflection, Stored Procedures, Modbus) se tocó en este incremento.

---

## 1. Decisión de diseño documentada (desviación deliberada del pedido literal)

El pedido original especificaba `extractor="SETTINGS_CLASS_CONNECTION"` y `pattern="Settings.Designer.cs"`. Ninguno de los dos se usó tal cual — es una corrección técnica, no un replanteo de arquitectura:

- **`SETTINGS_CLASS_CONNECTION` no existe en `analyzer/confidence.py: CONFIDENCE_TABLE`** (catálogo ya aprobado en Fase 1). Usarlo haría que `resolve_confidence()` cayera al piso `UNKNOWN=20` en vez del `95` que el catálogo ya asigna a este mecanismo exacto bajo el nombre `SETTINGS_DEFAULT_VALUE` — el propio comentario en `confidence.py` lo describe como *"el mecanismo dominante de descubrimiento de conexiones en este portafolio"*. Inventar un nombre nuevo hubiera sido asignar confidence a mano por la puerta de atrás, justo lo que la regla de diseño prohíbe.
- **ilspycmd emite el archivo como `Settings.cs`** en este portafolio (confirmado en los fixtures `happy_path`/`dedup_case`, ya documentado ahí citando un ejemplo real decompilado), nunca `Settings.Designer.cs`. Ese nombre real ya se captura en `source_file`; `pattern="DefaultSettingValue"` describe la regla que disparó (mismo estilo que `connectionStrings/add` del Incremento 1), no repite el nombre del archivo en otro campo.

Usado en su lugar: `extractor="SETTINGS_DEFAULT_VALUE"`, `pattern="DefaultSettingValue"`, `confidence=95` (vía `resolve_confidence()`, nunca a mano).

## 2. Archivos modificados

| Archivo | Cambio |
|---|---|
| `analyzer/extract.py` | `find_settings()` construye un `Evidence` real (línea, snippet, extractor, pattern, confidence, created_at) por cada entrada que sale del loop de `DefaultSettingValue` — conexiones, rutas locales y "otras" por igual, mismo punto de extracción para las tres. |
| `analyzer/db.py` | Sin cambios de esquema — ya usa las columnas de Evidence agregadas en el Incremento 1. |
| `analyzer/report.py` | Sin cambios — la tabla de "Connection strings" ya mostraba `Evidencia`/`Confianza` de forma genérica desde el Incremento 1; las entradas de Settings.cs simplemente empezaron a llegar con datos reales. |
| `analyzer/export_office.py` | Sin cambios — mismas columnas ya agregadas en el Incremento 1. |
| `tests/test_increment2_settingscs_evidence.py` | **Nuevo.** 8 tests: extracción real, deduplicación, persistencia sin migrar esquema, reporte Markdown, ruta Flask real (`test_client()`), export Excel, export Word, compatibilidad con filas históricas sin Evidence. |

## 3. Resultado de pytest

```
64 passed
```

(56 de la suite base + Incremento 1, + 8 nuevos de este incremento. Cero tests existentes modificados — no se demostró ningún error real en ellos.)

## 4. Validación funcional en vivo

Se levantó la app Flask real (`app.py`, puerto 5000) y se analizó `AFL.Dashboard/AFL.Dashboard` (app real del portafolio, no un fixture). La tabla "Connection strings" servida por la ruta real `/apps/<id>` muestra:

```
Setting  Valor por defecto                                              Archivo                         Evidencia                       Confianza
CX       Server=naamrt-QCS25; Database=QAPVMLN; User Id=QUALITY; ...    ClassLib.Properties\Settings.cs SETTINGS_DEFAULT_VALUE, linea 19  95%
```

(Captura de pantalla no disponible — limitación ya conocida del pane de este entorno con composición de frames; verificado en su lugar leyendo el DOM/texto renderizado real, mismo fallback ya usado y documentado en sesiones anteriores de este proyecto.)

## 5. Reanálisis completo del portafolio

La red (`\\naamrt-qcs25\...`) no está disponible desde esta sesión (confirmado con `Test-Path`). El reanálisis corrió **100% local** contra el código ya decompilado en `decompiled/` — no se volvió a decompilar nada ni se tocó ninguna app legada; solo se re-ejecutó el extractor Python ya actualizado sobre el mismo código fuente.

- **Backup tomado antes de mutar la BD real**: `qapv_analyzer.before_increment2_reanalysis.db` (raíz del proyecto).
- **61 / 61 apps reanalizadas, 0 fallidas, 0 no encontradas.**
- **Preservación explícita de lo que `save_analysis()` no preserva por sí solo**: `companion_assemblies`, y — más importante — `db_procedures`/`db_tables`/`db_intro_notes` (introspección real y de solo lectura contra SQL Server, no regenerable sin red). `save_analysis()` hace `DELETE FROM apps WHERE name=?` con `ON DELETE CASCADE`, así que sin este paso se habría perdido esa introspección. Confirmado preservado exacto: **87 stored procedures / 301 tablas, antes y después.**
- `review_status`/`review_notes` (juicio humano de revisión de lógica de negocio) sobrevivieron automáticamente — comportamiento ya garantizado desde antes de este incremento, confirmado visualmente en el sidebar (badges "Revisado"/"Revisado SP" intactos).

## 6. Reporte comparativo — Antes vs Después

| Métrica | Antes | Después |
|---|---|---|
| Apps analizadas | 61 | 61 |
| Total settings | 382 | 382 |
| Total sql_findings | 3,477 | 3,477 (sin cambio — fuera de alcance) |
| Total io_findings | 710 | 710 (sin cambio — fuera de alcance) |
| Total registros en `findings` (Hallazgos) | 206 | 206 (sin cambio) |
| Total `db_procedures` | 87 | 87 (preservado) |
| Total `db_tables` | 301 | 301 (preservado) |
| Settings con Evidence | 0 | **382 (100%)** |
| Settings desde `app.config` (`APP_CONFIG_EXPLICIT_CONNECTION`) | 0 | 13 |
| Settings desde `Settings.cs` (`SETTINGS_DEFAULT_VALUE`) | 0 | 369 |

### Distribución de confianza (settings)

| Rango | Antes | Después |
|---|---|---|
| > 90 | 0 | **382** |
| 80–90 | 0 | 0 |
| 50–80 | 0 | 0 |
| < 50 (y > 0) | 0 | 0 |
| Sin evidencia (UNKNOWN) | 382 | **0** |

Cobertura de Evidence sobre `settings`: **0% → 100%**. Con los dos extractores del Incremento 1 (app.config) + Incremento 2 (Settings.cs) combinados, **ya no queda ningún setting sin Evidence en todo el portafolio** — ambos mecanismos juntos son, en efecto, el 100% de cómo este portafolio declara sus conexiones/configuración.

### Distribución por extractor / patrón (después)

| Extractor | Cantidad | Patrón |
|---|---|---|
| `SETTINGS_DEFAULT_VALUE` | 369 | `DefaultSettingValue` |
| `APP_CONFIG_EXPLICIT_CONNECTION` | 13 | `connectionStrings/add` |

### Aplicaciones con mayor incremento de cobertura (mayor cantidad absoluta de settings recién instrumentados)

| App | Settings instrumentados |
|---|---|
| DataTransfer | 72 |
| VINS1/VINS1 | 68 |
| DataCenter2ORA | 25 |
| OTDR/OTDR | 19 |
| LightValidation/LightValidation | 18 |
| MPOEndFace - Copy (11) - Copy/EndFaceVal | 15 |
| LabelPrint/LabelPrint | 12 |
| FaceLabUnion/FaceLabUnion | 12 |
| FaceLab/FaceLab | 11 |
| MonitorFinalAudit/MonitorFinalAudit | 9 |

### Aplicaciones que siguen teniendo incertidumbre (gap real, distinto al de settings)

**Settings**: ninguna — cobertura 100% en las 61 apps.

**9 apps sin NINGÚN setting/conexión detectado** (gap preexistente, no atacado por ninguno de los dos extractores — candidatas a revisión manual, probablemente porque su conexión vive en un mecanismo no cubierto todavía, ej. hardcoded fuera de Settings.cs/app.config, o realmente no tienen BD propia):
`CentiServerMPO`, `CompareImages/App472`, `CompareImages/CompareImages`, `EpoxyLabel`, `EpoxyLabel/EpoxyLabel`, `INVENTA2-2TEST/AlmacenDiagnostico`, `INVENTA2-2TEST/InventaServerTest2`, `INVENTA2-2TEST/WebApplication1`, `VINS1/Modbus`.

(Nota: varias de estas ya están documentadas en memoria de sesiones previas como watchdogs/launchers sin SQL propio — ej. `CentiServerMPO` — así que "0 settings" no siempre implica un gap real, hay que revisar caso por caso.)

## 7. Análisis del portafolio — de dónde sale la recomendación del siguiente incremento

Por instrucción explícita: la prioridad se decide con datos del reanálisis, no con criterio arquitectónico ni asumiendo que "SQL dinámico" es lo siguiente. Métricas reales extraídas de la BD después del reanálisis:

| Área | Dato real |
|---|---|
| **Settings** (este incremento) | 382/382 con Evidence — **100% cerrado** |
| **Stored Procedures detectados** (`sql_findings.category='stored_procedure'`) | 311 — ya mayormente resueltos (nombre de SP + parámetros capturados desde antes; further gap ahí es de introspección BD, no de extracción) |
| **Queries (`category='query'`) — grupos (clase, método) donde el reporte final SOLO muestra el mensaje genérico "no resuelta automáticamente"** | **522 de 1,201 grupos (43.5%)**, repartidos en **42 de 61 apps (69%)** |
| Posible uso de Reflection (`Invoke(`/`Activator.CreateInstance`, heurística sobre `raw`) | 0 hallazgos detectados |

El número de 522 grupos es el que importa de verdad (no el conteo crudo de filas `sql_findings`, que infla el gap con líneas de boilerplate — declaraciones de `SqlConnection`/`SqlCommand` que ya se colapsan/ocultan en el reporte cuando existe un hermano resuelto en el mismo método): es literalmente cuántas funciones, hoy, muestran el mensaje genérico en el reporte real que ve el usuario, en vez de la query.

## 8. Recomendación fundamentada del siguiente incremento técnico

**Resolución de queries no resueltas (SQL dinámico / multi-línea)** es, con los datos reales del portafolio ya reanalizado, la brecha de mayor impacto disponible ahora:

- **Impacto sobre el portafolio**: 522 funciones en 42 apps (69% del portafolio) hoy no muestran ninguna query real en el reporte — es, en tamaño, un orden de magnitud mayor que cualquier otro gap medido (Reflection: 0; Settings: ya cerrado).
- **Reducción de incertidumbre**: settings pasó de 0% a 100% de cobertura de Evidence en este incremento; SQL/queries sigue en 0% de cobertura de Evidence y es la categoría de hallazgo MÁS grande del proyecto (3,477 sql_findings vs 382 settings) — el siguiente incremento con mayor retorno posible sobre el trabajo ya invertido en Evidence/Confidence es aplicar el mismo patrón ahí.
- **Cantidad de aplicaciones beneficiadas**: 42/61 (69%) tienen al menos un caso — más que cualquier otro extractor candidato.
- **Valor para la modernización**: la query real (no solo "hay una conexión aquí") es exactamente la especificación que se necesita para reimplementar cada función en el nuevo MES ([[feedback_sql_detail_level]] de sesiones anteriores ya lo estableció como requisito no negociable) — cerrar este gap tiene valor directo de negocio, no solo arquitectónico.

Esta recomendación se basa en los números de la sección 7, no en el orden "obvio" de VALIDATION_STRATEGY.md ni en la lista original de extractores pendientes (Reflection resultó, con datos reales, ser prácticamente inexistente en este portafolio — 0 hallazgos — así que NO se recomienda como siguiente paso pese a estar en el roadmap original).
