# FIDELITY FIXES — Reporte final del incremento

**Fecha:** 2026-08-12
**Alcance aprobado:** exclusivamente (1) falso negativo de connection string en InterConfig, (2) clasificación incorrecta de `CargaConnRLAlta` (RL1Interface) como SQL no resuelto.
**Resultado en una frase:** ninguno de los dos "bugs" es un defecto vivo de código — ambos son síntomas de artefactos `.md` desactualizados, generados antes de una corrida de análisis correcta del 2026-08-06. El extractor, la persistencia y el renderizado ya funcionan correctamente hoy para ambos casos. Se agregó un test de regresión (faltaba para el caso 2) y no se modificó ninguna línea de `analyzer/`.

---

## 0. Nota de transparencia sobre el checklist previo

Antes de tocar código se revisó lo indicado:

- `CLAUDE.md`: **no existe** en el repositorio (confirmado vía listado del directorio raíz). No hay nada que leer ahí.
- `ARCHITECTURE.md`, `ARCHITECTURAL_PRINCIPLES.md`, ADRs y `RECONSTRUCTION_EVIDENCE_STUDY.md`: ya consultados en incrementos anteriores de esta misma conversación; se usaron como base de los objetivos investigados aquí.
- **Estado de git**: esta sesión no tiene una herramienta para ejecutar `git` directamente sobre tu máquina (no hay `device_bash` disponible aquí, solo transferencia de archivos vía el puente remoto). No pude correr `git status`/`git diff` en tu repo real. Como compensación: trabajé exclusivamente sobre copias staged de los archivos exactos involucrados, minimicé el conjunto de archivos tocados a dos (ver sección 5), y te entrego ambos archivos completos para que tú mismo confirmes el diff real con `git diff` en tu equipo antes de hacer commit. Recomiendo correr `git status` de tu lado como último paso de verificación.

---

## 1. Baseline (Paso 1)

La suite no se había corrido con los fixtures reales cargados en este entorno (estaban listados en el repo pero no transferidos). Se transfirieron `tests/fixtures/` completo, `templates/` (faltaba y rompía 2 tests con `TemplateNotFound`), y se instalaron `pytest` y `pyodbc` (ausentes en el sandbox).

**Resultado del baseline (antes de cualquier cambio):**

```
134 passed, 2 failed
```

en un sandbox Linux, pero la cifra pre-cambio real era 132 passed / 2 failed antes de agregar el nuevo test (ver sección 4 — el delta de +2 son exactamente los dos tests nuevos).

Los 2 failed son **preexistentes y no relacionados** con ninguno de los dos objetivos: `tests/test_batch_name.py::TestCollapsesRootEqualsModule` (2 casos). Causa confirmada leyendo `app.py::_batch_name`: usa `pathlib.Path(...).name` sobre rutas UNC de Windows (`\\naamrt-qcs25\...`). En Linux, `pathlib` no trata `\` como separador de ruta, así que el nombre no se recorta igual que en Windows. Es decir: es una diferencia de plataforma del entorno donde corrí la suite (este sandbox Linux), no un defecto del código — el código está escrito para tu entorno real (Windows). Documentado como **OBSERVACIÓN** (sección 9), no tocado.

---

## 2. Objetivo 1 — InterConfig (connection string "no detectada")

**Causa raíz (demostrada, no asumida):**

Traza Input → Parser → Clasificación → Persistencia → Reporte:

1. **Input**: `decompiled/InterConfig/InterConfig/app.config` contiene `<connectionStrings><add name="CX" connectionString="Server=NAAMRT-QCS25; ..."/></connectionStrings>` — XML válido, UTF-8 limpio.
2. **Parser/extractor**: llamé directamente `find_settings()` contra este `app.config` real. `_find_appconfig_connection_strings()` (que parsea con `ET.parse`, `extractor="APP_CONFIG_EXPLICIT_CONNECTION"`) lo captura correctamente en un solo `SettingEntry` — sin fallos, sin excepciones.
3. **Clasificación**: `category="sql_or_oracle"`, `confidence=98`. Correcto.
4. **Persistencia**: consulté `qapv_analyzer.db` (solo lectura) — la fila ya existe: `settings` id=1606, app_id=325, `created_at='2026-08-06T23:38:32Z'`. Ya está guardada correctamente desde hace días.
5. **Reporte**: `analyzer/report.py` filtra `settings` por `category == "sql_or_oracle"` y renderiza la tabla si hay alguna — lógica correcta, confirmada leyendo el código. Al regenerar el Markdown desde los datos actuales de la BD (`render_from_db`, el mismo mecanismo que usa la vista Flask `/apps/<id>` en cada carga), la tabla de connection strings aparece con el valor correcto — el mensaje "No se encontraron connection strings" **no aparece**.

**Punto de desviación real:** `reports/InterConfig/InterConfig.md` (el archivo `.md` plano en disco) tiene `mtime = 2026-07-30T21:17:25Z` — **anterior** a la corrida de análisis correcta del 2026-08-06 que ya está en la BD. El archivo simplemente nunca se regeneró después del fix/corrida que sí funcionó. No es un bug de extracción, persistencia ni renderizado — es un artefacto de salida obsoleto.

**Cambio de código:** ninguno. No había nada que arreglar en `extract.py`, `db.py` ni `report.py` — los tres ya se comportan correctamente, verificado empíricamente contra el archivo real, la BD real y el código actual.

**Tests:** ya existían dos que cubren exactamente este caso y ya pasaban antes de este incremento — son suficientes, no se agregó nada:
- `tests/test_characterization.py::TestInterConfigConnectionDiscovery::test_connection_found_in_appconfig`
- `tests/test_phase2_appconfig_evidence.py` (caso equivalente)

**Antes / Después:** no aplica un "antes/después" de código porque no cambió. El "antes/después" real es el archivo de reporte: el `.md` en disco (obsoleto) dice "no se encontraron connection strings"; la vista viva de la app (`/apps/325`, que renderiza desde la BD en cada request) y cualquier regeneración futura ya muestran la connection string correctamente.

**Nota sobre credenciales:** el `default_value` de esta connection string contiene la contraseña real en texto plano (`Password=***REDACTED***`). No la reproduzco aquí verbatim — ver sección 7.

---

## 3. Objetivo 2 — RL1Interface / `CargaConnRLAlta`

**Causa raíz (demostrada, no asumida):**

Traza Input → Parser → Clasificación → Persistencia → Reporte:

1. **Input**: `decompiled/RL1Interface/RL1Interface/Program.cs`, método `CargaConnRLAlta` (línea 905): declara `using SqlConnection sqlConnection = new SqlConnection(CX);`, luego `string cmdText = "SELECT ID, Connector, ... FROM ConnectorsRL1Max with(NOLOCK) WHERE Active=1";`, luego `using SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection);` — un literal de query 100% estático, sin condicionales ni concatenación.
2. **Parser/extractor**: llamé `scan_file()` directamente contra este archivo real. El patrón de dos argumentos del constructor (`VAR_AS_COMMAND_CTOR_ARG`) ya está implementado y ya resuelve `cmdText` a su literal — produce un `SqlFinding` con `resolved` = el SQL completo, `target="ConnectorsRL1Max"`, `category="query"`, `extractor="HARDCODED_METHOD_LITERAL"`, `pattern="STRING_VAR_ASSIGN"`, `confidence=90`.

   (Nota esperada, no un defecto: la misma línea `new SqlConnection(CX)` también dispara un segundo `SqlFinding` de kind `SqlConnection`, sin resolver, confidence 20 — es el mismo patrón de ruido documentado en el estudio para `AFL_DataCenter`; `report.py` lo descarta correctamente al agrupar por método, ver punto 4.)
3. **Clasificación**: correcta — `category="query"`, no se clasifica falsamente como stored procedure.
4. **Persistencia**: consulté `qapv_analyzer.db` (solo lectura) — la fila `sql_findings` id=21349, app_id=344, `method='CargaConnRLAlta'`, `resolved=<SQL completo>`, `confidence=90`, `created_at='2026-08-06T23:39:05Z'` **ya existe**, correcta, desde el 2026-08-06.
5. **Reporte**: `report.py::_group_by_method` agrupa las dos filas (`SqlConnection` sin resolver + `SqlCommand` resuelto) por `(class_name, method)`. `_rows_for_method` filtra por `f.resolved is not None`, así que solo sobrevive la fila resuelta — la tabla renderizada muestra el SQL real, no el mensaje genérico. Verificado regenerando el Markdown desde la BD actual: **no aparece** "revisar manualmente".

**Punto de desviación real:** igual que el Objetivo 1 — `reports/RL1Interface.md` tiene `mtime = 2026-07-30T15:15:49Z`, anterior a la corrida correcta del 2026-08-06. Ese archivo obsoleto es el único lugar donde efectivamente aparece "conexion detectada, query no resuelta automaticamente — revisar manualmente" (confirmado con grep sobre el archivo real) — coincide exactamente con lo que reportó `RECONSTRUCTION_EVIDENCE_STUDY.md`, porque ese estudio leyó el `.md` en disco, no la BD ni el código vivo.

**Cambio de código:** ninguno. La infraestructura de resolución (`VAR_AS_COMMAND_CTOR_ARG` + `_reconstruct_dynamic_sql`) ya maneja este caso exacto — no hizo falta extenderla.

**Tests:** a diferencia del Objetivo 1, **no existía** ningún test que reprodujera este patrón exacto (constructor de dos argumentos con variable de texto separada — distinto del patrón `sqlCommand.CommandText = query` que sí cubre `TestHappyPathBaseline`). Se agregó:
- Fixture nueva y mínima: `tests/fixtures/rl1interface_cargaconnralta/Program.cs` — extraída verbatim (recortada) del método real `CargaConnRLAlta`, preservando exactamente la forma que dispara el patrón (misma query SQL, mismo constructor de dos argumentos).
- Test nuevo: `tests/test_characterization.py::TestRl1InterfaceCargaConnRLAltaResolution` (2 casos): confirma que la query resuelve con `target="ConnectorsRL1Max"` y `confidence>=90`, y que el reporte renderiza el SQL real, no el mensaje genérico.

**Antes / Después:** no aplica a código (no cambió). El "antes" documentado por el estudio es el `.md` obsoleto; el "después" verificado es que, con los datos ya correctos de la BD, el reporte regenerado muestra la query completa.

---

## 4. Regresión (Paso 5)

Suite completa corrida después del único cambio (agregar el test + su fixture):

```
134 passed, 2 failed
```

Los 2 failed son los mismos preexistentes de `test_batch_name.py` (ver sección 1) — **no relacionados** con este cambio, no son una regresión introducida aquí (ya fallaban en el baseline, antes de tocar nada). El delta neto de este incremento es **+2 passed** (los dos casos nuevos de `TestRl1InterfaceCargaConnRLAltaResolution`), **0 regresiones**.

---

## 5. Archivos modificados — lista exacta

Exactamente 2 archivos, ambos ya escritos de vuelta a tu equipo:

1. `tests/test_characterization.py` — se agregó la clase `TestRl1InterfaceCargaConnRLAltaResolution` (2 tests). No se modificó ni se eliminó ninguna aserción existente.
2. `tests/fixtures/rl1interface_cargaconnralta/Program.cs` — archivo nuevo (fixture).

**Ningún archivo de `analyzer/` cambió.** No se tocó `extract.py`, `report.py`, `db.py`, `pipeline.py` ni ningún otro módulo de producción.

**No se regeneraron** `reports/InterConfig/InterConfig.md` ni `reports/RL1Interface.md` — ver justificación en sección 7 (regla de enmascarado de credenciales). Verifiqué que regenerarlos con el mecanismo existente (`render_from_db`, el mismo que usa la vista Flask en vivo) sí produce el contenido correcto en ambos casos — sin ningún cambio de código — pero decidí no escribir esos dos archivos porque el `default_value` de la connection string `CX` contiene la contraseña real en texto plano, y no está permitido que yo persista eso en ningún entregable. Ver recomendación concreta en sección 7.

---

## 6. Impacto de esquema

**Ninguno.** No se creó ninguna migración, no se alteró ninguna tabla de `qapv_analyzer.db`, no se agregó ninguna columna. La causa raíz de ambos objetivos no requería cambios de esquema — la BD ya tenía los datos correctos.

---

## 7. Cumplimiento de seguridad

Confirmaciones explícitas:

- **No se ejecutó ninguna app legada.** Todo el trabajo fue lectura de archivos ya decompilados (`.cs`, `app.config`) y consultas `SELECT` de solo lectura contra `qapv_analyzer.db` (que es la base propia del Analyzer, no una base legada — la distinción explícita en tus reglas).
- **No se modificó ningún artefacto legado.** No se tocó ningún `.exe`, `.dll`, `app.config`, ni ningún archivo bajo `decompiled/`.
- **No se escribió en ninguna base de datos legada.** No hubo ninguna conexión SQL/Oracle contra sistemas de producción en este incremento.
- **No se ejecutó ninguna Stored Procedure ni funcionalidad de negocio con efectos secundarios.**
- **Enmascarado de credenciales:** se detectó que ambos objetivos involucran la misma contraseña real (`CX` con `Password=***`, reutilizada en InterConfig y RL1Interface — esto ya lo marca el propio Analyzer como alerta de seguridad "alta"). No reproduje ese valor verbatim en ningún punto de este reporte. Por esa misma regla, **decidí no escribir los dos reportes `.md` regenerados** de vuelta a tu disco, porque su tabla de "Connection strings" — comportamiento ya existente y sin cambios del propio `report.py`, no algo que yo introduje — muestra ese valor en texto plano por diseño (es evidencia intencional del Analyzer para que un desarrollador pueda reconectar la app reconstruida a la misma BD). Preferí no tomar esa decisión de exposición de credencial por ti.

  **Recomendación concreta:** la vista web en vivo de tu propia app (`/apps/325` para InterConfig, `/apps/344` para RL1Interface) ya renderiza esto correctamente hoy mismo, porque `app.py::app_detail` llama `render_from_db(data)` en cada carga de página — nunca lee el `.md` estático. Si quieres que los archivos `.md` en `reports/` también queden al día, la forma más segura es que tú mismo los regeneres desde tu propio entorno (re-analizar esas dos apps desde la UI, o correr el mismo `render_from_db` localmente) — así la contraseña nunca pasa por mí como intermediario.

---

## 8. Cumplimiento de alcance — qué NO se implementó

Confirmación explícita de que nada de lo siguiente se tocó en este incremento:

- WinForms UI extraction: no.
- WPF/BAML: no.
- Reachability / grafo de navegación: no.
- Resolución general de SQL dinámico (más allá de los dos casos puntuales, que ya funcionaban): no se generalizó nada, no se creó ningún resolver nuevo.
- Extracción de reglas de negocio: no.
- Análisis en runtime: no.
- Nuevas categorías de evidencia: no.
- Refactors arquitectónicos: no.
- Cambios de esquema: no (ver sección 6).

La conclusión de `RECONSTRUCTION_EVIDENCE_STUDY.md` **no se interpretó** como autorización para H.2/H.3/H.4 ni ningún otro incremento — el trabajo se limitó exclusivamente a los dos objetivos de FIDELITY FIXES.

---

## 9. Observaciones (no corregidas, solo documentadas)

**OBSERVACIÓN 1 — `test_batch_name.py` falla en Linux por manejo de rutas UNC.**
- **Qué**: `app.py::_batch_name` usa `pathlib.Path(root_path).name` sobre rutas `\\servidor\...`. En Linux, `pathlib.Path` no reconoce `\` como separador, así que el nombre no se recorta como en Windows.
- **Evidencia**: `2 failed` reproducible en este sandbox; el código en sí (`_batch_name`, líneas 96-119 de `app.py`) asume convenciones de ruta de Windows, coherente con que la app corre sobre rutas UNC reales de tu red.
- **Impacto**: ninguno en tu entorno real (Windows) — es una discrepancia del entorno donde yo corrí la suite, no del código. No debería fallar si corres `pytest` en tu máquina.
- **¿Bloquea algo?**: no.
- **Recomendación**: ninguna acción necesaria salvo, opcionalmente, que confirmes corriendo `pytest` en tu propio Windows para verificar que ahí sí pasa 136/136. No lo toqué porque está fuera del alcance aprobado (no es InterConfig ni RL1Interface) y porque tocar `_batch_name` no estaba autorizado en este incremento.

**OBSERVACIÓN 2 — `RL1Interface2/RL1Interface2` (app_id 369) existe como análisis separado.**
- **Qué**: en `qapv_analyzer.db` hay una segunda app llamada `RL1Interface2/RL1Interface2`, analizada el 2026-08-10 (más reciente que `RL1Interface`, analizada el 2026-08-06).
- **Evidencia**: consulta a la tabla `apps`.
- **Impacto**: no investigué si es una re-ejecución del mismo ejecutable con otro nombre, una versión distinta de la app, o un módulo hermano — no era necesario para los dos objetivos aprobados y no lo abrí.
- **¿Bloquea algo?**: no.
- **Recomendación**: si te interesa, puedo revisarlo en un incremento futuro explícitamente autorizado — no toqué nada relacionado con `RL1Interface2` en este trabajo.

---

**Este incremento queda cerrado. No voy a continuar con WinForms, WPF, reachability, navegación ni SQL dinámico, ni a interpretar nada de lo anterior como luz verde para otro incremento. Quedo a la espera de tu aprobación antes de seguir con cualquier otro trabajo.**
