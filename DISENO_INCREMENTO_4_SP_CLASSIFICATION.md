# Diseño técnico — Incremento 4: clasificación correcta de Stored Procedures

Estado: propuesta, NO implementada. Continúa directamente la investigación ya
cerrada durante la validación E2E de Incremento 3 (ver conversación previa),
donde se descubrió el bug de `command_type_is_sp_nearby` en
`analyzer/extract.py::_classify_sql()` mientras se revisaba el badge "SP" de
`AFL_DataCenter`. Ese bug es preexistente a Incremento 3, no fue introducido
por él, y queda formalmente fuera de su alcance (ya cerrado en el commit
`488017d`).

## 0. Resumen de la propuesta

Atar la evidencia de `stored_procedure` a la **variable de comando concreta**
que se está clasificando, reutilizando el mismo mecanismo de "variable +
forward-scan acotado al método" que `_extract_parameters()`/
`_extract_result_columns()` ya usan hoy para parámetros y columnas — en vez
de la ventana ciega de líneas (`lines[idx-5:idx+8]`) que usa hoy
`command_type_is_sp_nearby`. Ningún camino de detección por nombre limpio
(`BARE_PROC_NAME`, `SCHEMA_QUALIFIED_PROC`) cambia — sigue funcionando igual,
porque nunca dependió de la ventana ciega.

## 1. Problema

### Causa raíz (confirmada leyendo código real, no solo inferida)

`_classify_sql(text, command_type_is_sp_nearby)` (`analyzer/extract.py:650`)
tiene dos caminos hacia `category="stored_procedure"`:

- **Camino A — por nombre** (línea 664, rama `not has_keyword`): el primer
  literal entre comillas tiene forma de identificador (`BARE_PROC_NAME` o
  `SCHEMA_QUALIFIED_PROC`) y el texto no contiene una palabra clave SQL. Este
  camino **no depende de `command_type_is_sp_nearby`** y siempre produce un
  nombre limpio.
- **Camino B — por cercanía** (líneas 675-676): si
  `command_type_is_sp_nearby=True`, se entra a la rama sin importar si hay
  palabra clave SQL, y si no hay `name_match`, se hace
  `return "stored_procedure", text.strip()[:60], True` — usa los primeros 60
  caracteres del texto CRUDO como "nombre del SP". Este camino nunca produce
  un dato útil: o hay `name_match` (y entonces el camino A ya lo hubiera
  resuelto igual) o no lo hay (y el resultado es basura).

`command_type_is_sp_nearby` se calcula así (`analyzer/extract.py:752-753`),
para **cada trigger SQL del archivo, independientemente**:

```python
context_window = " ".join(lines[max(0, idx - 5): min(len(lines), idx + 8)])
sp_nearby = bool(STORED_PROC_TYPE.search(context_window))
```

Es una ventana ciega de líneas (5 antes, 8 después de la línea del trigger
actual) que **nunca verifica a qué variable de comando pertenece** el
`CommandType.StoredProcedure` encontrado.

### Ejemplos reales confirmados (todos verificados leyendo el `.cs` decompilado, ver conversación previa)

| App | Método | Trigger contaminado | SP real cercano | Distancia |
|---|---|---|---|---|
| `AFL_DataCenter` | `NewImportDJ` | `oracleCommand2.CommandText = "...SELECT..."` (línea 1074, SELECT real a Oracle) | `sqlCommand2.CommandType = CommandType.StoredProcedure;` (línea 1080, de `UpdateDJItem`) | 6 líneas |
| `AFL_DataCenter` | `NewImportDisp` | `using SqlConnection sqlConnection = new SqlConnection(constring);` (línea 1671, apertura de conexión) | `sqlCommand.CommandType = CommandType.StoredProcedure;` (línea 1677, de `UpdateDispositions`) | 6 líneas |
| `AFL.Dashboard/*` (7 apps, mismo `ClassLib\Class1.cs` compartido) | `GetHistoricoOperaciones` | `SqlConnection sqlConnection = new SqlConnection(CX);` (línea 1307) | `sqlCommand.CommandType = CommandType.StoredProcedure;` (línea 1312, de `[dbo].[GetHistoricoOperaciones]`) | 5 líneas |
| `INVENTA2-2TEST/SGI` | `ObtenerUsuarios` | `using (SqlConnection sqlConnection = new SqlConnection(connectionString)) { try { using SqlCommand sqlCommand = new SqlCommand();` (captura de `_capture_statement` colapsando varias líneas, ~140) | `sqlCommand.CommandType = CommandType.StoredProcedure;` (línea 143, de `[dbo].[ObtenerUsuarios]`) | 3 líneas |

## 2. Invariante formal

1. `SqlConnection` / `OracleConnection` **nunca** pueden clasificarse como
   `stored_procedure`. Abrir una conexión no ejecuta ningún comando; no tiene
   texto de comando en absoluto.
2. Un `SELECT`/`INSERT`/`UPDATE`/etc. de un comando **no puede heredar**
   `stored_procedure` porque exista un `CommandType.StoredProcedure` cercano
   perteneciente a OTRO comando.
3. Un comando cuya propia variable tiene `CommandType.StoredProcedure`
   asignado **debe seguir** clasificándose como `stored_procedure` — esto no
   cambia.
4. El nombre del SP debe proceder del texto (constructor o `.CommandText =`)
   atado a la MISMA variable de comando que tiene `CommandType.StoredProcedure`
   — nunca del literal de un comando distinto.
5. Si no existe evidencia suficiente (no se puede determinar la variable, o
   no hay `CommandType.StoredProcedure` atado a ella), **no se debe inventar**
   `category="stored_procedure"` — debe caer al comportamiento normal
   (`query`, tabla detectada, o default), igual que si `sp_nearby` no
   existiera.

## 3. Solución propuesta

Reutilizar exactamente lo que ya existe para parámetros/columnas:

- `CMD_VAR` (`analyzer/extract.py:310`) ya extrae, desde `raw`, la variable
  de comando del trigger actual (`sqlCommand`, `sqlCommand2`, etc.) — usado
  hoy en `scan_file()` línea 758 solo para `_extract_parameters()`/
  `_extract_result_columns()`.
- `_find_method_end()` (línea 323) ya acota cualquier forward-scan al cierre
  real del método (por profundidad de llaves), evitando que el escaneo
  "se cuele" al método siguiente — el mismo problema, en espíritu, que la
  ventana ciega de `sp_nearby` tiene hoy pero sin resolver.

**Propuesta concreta**: en `scan_file()`, en vez de calcular
`context_window`/`sp_nearby` con una ventana de líneas ciega, calcular
`command_type_is_sp_nearby` así:

1. Si `kind` es `"SqlConnection"` u `"OracleConnection"` → `sp_nearby = False`
   siempre, sin ninguna búsqueda. (Ya no hace falta ni inventar una regla
   nueva: `CMD_VAR.search(raw)` YA falla para estos triggers hoy — un
   `new SqlConnection(...)` no matchea ni `.CommandText =` ni
   `(?:Sql|Oracle)Command\s+\w+\s*=`. Es decir, el dato para excluirlos ya
   existe, solo no se está usando para esto.)
2. Para el resto de triggers: obtener `cmd_var` con `CMD_VAR.search(raw)`
   (mismo mecanismo ya usado en la línea 758). Si no hay `cmd_var`,
   `sp_nearby = False` (regla 5 del invariante: sin variable identificable,
   no hay evidencia suficiente).
3. Si hay `cmd_var`: buscar, dentro de `lines[idx : _find_method_end(lines, method_start_idx)]`
   (mismo límite ya usado por `_extract_parameters`), una asignación
   `{cmd_var}\.CommandType\s*=\s*CommandType\.StoredProcedure` — solo sobre
   ESA variable, no sobre el texto completo de la ventana.
4. Eliminar el fallback `text.strip()[:60]` de la línea 676: si
   `command_type_is_sp_nearby` es la única señal (no hubo `name_match`) y no
   hay un nombre resoluble, **no** devolver `stored_procedure` — dejar que el
   flujo siga a `TABLE_UPDATE`/`TABLE_FROM`/`TABLE_INTO` o al `"query"` por
   defecto (líneas 678 en adelante, sin tocar esa lógica).

### Por qué esta solución es preferible a ampliar la ventana heurística

Ampliar o encoger la ventana de líneas (ej. de ±5/+8 a ±2/+3) solo movería el
punto de falla, no lo eliminaría — seguiría siendo ciega a la identidad de la
variable, y cualquier método corto (como los de `AFL.Dashboard`/`SGI`, con la
apertura de conexión a 3-6 líneas del `CommandType.StoredProcedure`) seguiría
contaminándose con una ventana más chica, mientras que una ventana más grande
solo aumentaría el radio de contaminación entre comandos legítimamente
distintos en métodos largos como `DataCenter.cs`. Atar la búsqueda a la
variable es la única forma de resolver la causa raíz (evidencia atada a la
operación concreta, no a la proximidad textual) sin inventar una heurística
nueva — es la reutilización directa de un patrón que este mismo archivo ya
usa y ya está implícitamente validado por los tests de parámetros/columnas
existentes.

## 4. Compatibilidad — qué debe conservarse exactamente igual

- **Stored procedures reales por nombre limpio** (`new SqlCommand("Nombre", conn)`,
  `cmd.CommandText = "Nombre '" + arg + "'"`, `cmd.CommandText = "[dbo].[Nombre]"`):
  siguen resolviéndose por el Camino A, sin cambio — no dependen de
  `sp_nearby`.
- **Queries normales** (`category="query"`, con o sin tabla detectada): sin
  cambio en su lógica de detección (`TABLE_UPDATE`/`TABLE_FROM`/`TABLE_INTO`).
- **`oracle_package_call`**: el `pkg_match` de `ORACLE_PKG_CALL` se evalúa
  ANTES que cualquier lógica de SP (línea 652-654) — no se toca.
- **Extracción de parámetros y result columns** (`_extract_parameters`,
  `_extract_result_columns`): no se modifican; el cambio propuesto solo
  reutiliza su mismo mecanismo de acotamiento por método, no altera su
  código.
- **`kind` de cada finding** (`SqlConnection`/`OracleConnection`/`CommandText`/
  `SqlCommand`/`OracleCommand`): sin cambio — el fix solo afecta `category`/
  `target`/`is_stored_procedure`, nunca `kind`.
- **Evidence/confidence** (`analyzer/evidence.py`, `analyzer/confidence.py`):
  no se tocan.

## 5. Alcance — explícitamente fuera de este incremento

- Clasificación de carpetas de Incremento 3 (`analyzer/classification.py`,
  `analyzer/pipeline.py`) — ya cerrado, no se modifica.
- El blocklist `THIRD_PARTY_ASSEMBLY_PATTERN` de `analyzer/decompile.py` — no
  se toca ni se amplía.
- Reconstrucción de SQL dinámico (`_reconstruct_dynamic_sql`,
  `_tokenize_string_expression`, etc.) — Incremento 3A, no se toca.
- Reanálisis del portafolio completo.
- Reparación inmediata de las 204 filas históricas ya persistidas (ver
  sección 8 — es una decisión de gobernanza separada, posterior a corregir
  el extractor).

## 6. Plan de tests

Fixture nuevo propuesto: `tests/fixtures/sp_classification_case/`, con un
único método que reproduzca, en orden, las 4 operaciones que pidió la
validación:

```csharp
public void MetodoDeEjemplo()
{
    using SqlConnection connection = new SqlConnection(constring);          // 1. apertura de conexion
    using SqlCommand selectCmd = new SqlCommand("SELECT TOP 1 X FROM T", connection);  // 2. SELECT normal
    connection.Open();
    using OracleConnection oraConn = new OracleConnection(oradb);           // variante cross-tech
    using OracleCommand oraCmd = oraConn.CreateCommand();
    oraCmd.CommandText = "SELECT Y FROM ORA_VIEW";                          // otro SELECT, distinto comando
    using SqlCommand spCmd = new SqlCommand();
    spCmd.Connection = connection;
    spCmd.CommandText = "UpdateAlgo";
    spCmd.CommandType = CommandType.StoredProcedure;                       // 3. creacion/config del SP
    spCmd.Parameters.Add("@x", SqlDbType.Int).Value = 1;
    spCmd.ExecuteNonQuery();                                                // 4. ejecucion del SP
}
```

| # | Test | Resultado esperado |
|---|---|---|
| 1 | Conexión (`SqlConnection`) cercana a un SP real | `category != "stored_procedure"` para el finding de `new SqlConnection(...)` |
| 2 | `SELECT` (vía `SqlCommand`/`CommandText`) cercano a un SP real | `category == "query"` (o tabla detectada), nunca `"stored_procedure"` |
| 3 | El comando que sí tiene `CommandType.StoredProcedure` | `category == "stored_procedure"`, `is_stored_procedure == True` |
| 4 | Nombre del SP corresponde al comando correcto | `target == "UpdateAlgo"` exactamente, no el texto del SELECT ni de la conexión |
| 5 | Dos comandos SP distintos cercanos (ej. `spCmd1`/`spCmd2` cada uno con su propio `CommandType.StoredProcedure` y nombre distinto) | Cada finding resuelve su PROPIO nombre — ninguno hereda el del otro |
| 6 | `OracleCommand` (SELECT) + `SqlCommand` (SP) cercanos entre sí | El `OracleCommand` sigue siendo `query`/`oracle_package_call` según corresponda; el `SqlCommand` sigue siendo `stored_procedure` — cero contaminación cruzada |
| 7 | Comando sin `CommandType.StoredProcedure` en absoluto y sin nombre limpio (ej. `cmd.CommandText = variableNoResoluble;`) | No se fuerza `stored_procedure` — cae a `query`/default |
| 8 | Casos ya cubiertos hoy por nombre limpio (`new SqlCommand("Nombre", conn)`, `"Nombre '" + arg`, `"[dbo].[Nombre]"`) | Sin cambio de comportamiento — mismos resultados que hoy |

Reproducción directa de regresión: agregar además un test que replique
literalmente (en el fixture aislado, no contra `decompiled/` real) la forma
exacta de `NewImportDisp` (conexión + comando SP a 6 líneas) y de `NewImportDJ`
(SELECT Oracle + comando SP a 6 líneas), confirmando que ninguno de los dos
queda contaminado.

## 7. Riesgo de regresión

Variantes reales confirmadas en el portafolio que el diseño debe cubrir
explícitamente (sin implementar nada todavía):

- **`CommandType` asignado varias líneas después de `CommandText`** (patrón
  dominante: `new SqlCommand(); cmd.CommandText = "..."; cmd.CommandType = CommandType.StoredProcedure;`
  en 3 statements separados) — confirmado en `AFL_DataCenter` y
  `INVENTA2-2TEST/SGI`. El forward-scan acotado al método (no una ventana
  fija) debe cubrir esta distancia variable sin límite artificial de líneas.
- **`CommandText` asignado separadamente, con nombre YA limpio** (ej.
  `sqlCommand.CommandText = "[dbo].[ObtenerUsuarios]";`) — estos casos hoy
  YA se resuelven por nombre (Camino A) independientemente del bug; el fix no
  debe alterar ese camino.
- **Nombre del SP fuera del constructor** — confirmado (`CommandText =`
  como statement separado del `new SqlCommand()`), ya cubierto arriba.
- **`OracleCommand`** — confirmado en `AFL_DataCenter` (`NewImportDJ`,
  `NewImportItemCatalog`, etc.): el SELECT real usa `OracleCommand`, el SP
  usa `SqlCommand`, ambos triggers distintos en el mismo método. El fix debe
  garantizar que la búsqueda de `CommandType.StoredProcedure` quede atada a
  la variable del comando que se está clasificando, sin importar si es
  `Sql*` u `Oracle*`.
- **`SqlCommand`** — caso base, ya cubierto en todos los ejemplos.
- **Concatenación** (`"Nombre '" + arg + "'"`) — ya funciona hoy vía
  `BARE_PROC_NAME` sobre el primer literal; no depende de `sp_nearby`, el fix
  no debe tocar esa ruta.
- **Captura de statement que colapsa varias líneas de C# en un solo `raw`**
  (`_capture_statement`, visto en `SGI`/`FaceLab`: `using (SqlConnection... ) { try { using SqlCommand ... = new SqlCommand();`
  todo en un solo `raw` porque el `using(...)` no cierra con `;` antes de la
  siguiente declaración) — esto es una imprecisión de captura DISTINTA y
  preexistente (no es el bug de `sp_nearby`, pero interactúa con él: si un
  `raw` de tipo `SqlConnection` incluye texto de un `SqlCommand` dentro,
  `CMD_VAR` podría en teoría encontrar una variable donde no debería). Debe
  verificarse durante la implementación que el gate por `kind` (regla 1 del
  diseño: `SqlConnection`/`OracleConnection` → `sp_nearby=False` sin
  excepción) cubre este caso sin depender de que `CMD_VAR` falle
  correctamente — es una defensa en profundidad necesaria, no opcional.
- **Posible mejora colateral, a confirmar en tests, no a diseñar ahora**: el
  forward-scan acotado al método tiene mucho más alcance (hasta el cierre
  real del método) que la ventana ciega actual (+8 líneas) — es posible que
  esto además CORRIJA casos hoy no detectados como SP porque su
  `CommandType.StoredProcedure` cae fuera de la ventana actual. Si aparece
  durante la implementación, debe reportarse como hallazgo nuevo, no
  asumirse de antemano.

## 8. Migración de datos históricos (propuesta de gobernanza, NO ejecutada)

Corregir el extractor y corregir las 204 filas ya persistidas son **dos
operaciones distintas**, con niveles de riesgo distintos (código vs.
conocimiento ya almacenado — Principio 1, `ARCHITECTURAL_PRINCIPLES.md`: los
datos regenerables se reemplazan sin miedo, pero cualquier cambio a datos ya
persistidos en producción necesita su propio gate, igual que Incremento 3
tuvo el suyo).

Propuesta de secuencia, para cuando el extractor ya esté corregido y probado:

1. **Identificación exacta de filas contaminadas**: reutilizar la misma
   consulta ya usada en esta investigación (`kind IN ('SqlConnection','OracleConnection') AND category='stored_procedure'`
   es 100% inequívoca; ampliar con `is_stored_procedure=1 AND target` con
   forma sospechosa para el resto) — documentar el conjunto EXACTO de `id`s
   afectados antes de tocar nada, como snapshot de auditoría.
2. **Re-análisis aislado, no en producción**: mismo patrón ya usado en la
   validación E2E de Incremento 3 — `decompiled/` temporal, mismo `.exe`,
   sin escribir en `qapv_analyzer.db` de producción, para cada una de las 16
   apps afectadas.
3. **Comparación antes/después por clave exacta** (mismo mecanismo ya
   probado: comparar por `(file, class_name, method, kind, raw)`, no solo
   por conteo) — para cada app, confirmar que el NUEVO resultado:
   - conserva el mismo número total de `sql_findings` (el fix de
     clasificación no debe cambiar CUÁNTOS triggers se detectan, solo CÓMO
     se categorizan),
   - reclasifica exactamente las filas ya identificadas en el paso 1 (y
     ninguna otra) de `stored_procedure` a `query`/tabla/default,
   - no introduce ninguna fila nueva ni elimina ninguna existente.
4. **Nunca borrar filas** — solo `UPDATE` de `category`/`target`/
   `is_stored_procedure` sobre los `id`s ya identificados en el paso 1,
   preservando el resto de la fila (línea, snippet, evidencia) intacto.
5. **Gate obligatorio antes de actualizar la BD real**: igual que el gate
   anti-falso-negativo de Incremento 3 — el `UPDATE` propuesto solo se activa
   si la comparación del paso 3 es 100% determinista y reproducible en las
   16 apps, y si un humano revisó una muestra representativa de los cambios
   (no solo el conteo agregado).
6. **Auditoría de los cambios**: conservar un registro de qué filas se
   modificaron y con qué valores anteriores (ej. un export a JSON de las
   filas afectadas ANTES del `UPDATE`, guardado fuera de la BD) — permite
   revertir o auditar sin depender de que la BD en sí lleve historial de
   cambios, que no lo lleva.

No se ejecuta nada de esto en Incremento 4 — Incremento 4 termina cuando el
extractor está corregido y probado; la migración de datos históricos, si se
aprueba, sería un Incremento 5 separado.

## 9. Criterio de aceptación de Incremento 4

- Todos los tests nuevos de la sección 6 pasando.
- Suite completa (`pytest -q`) sin regresiones (mismo total ya conocido +
  los tests nuevos, cero fallos).
- Los 2 casos reales de `AFL_DataCenter` (`NewImportDJ`, `NewImportDisp`)
  corregidos, verificado ejecutando el extractor corregido contra el
  `decompiled/AFL_DataCenter/` YA existente (sin reanalizar, sin re-decompilar
  — solo re-ejecutar `scan_project()`/`find_settings()` contra los archivos
  ya presentes en disco, en un script aislado, igual que la validación E2E).
- Cero findings con `kind IN ('SqlConnection','OracleConnection')` y
  `category='stored_procedure'` en esa verificación.
- Cero SELECT (`category` distinto de `stored_procedure` en su forma
  original) contaminados.
- Los SPs reales conocidos (`UpdateDJItem`, `UpdateDispositions`,
  `GetHistoricoOperaciones`, `[dbo].[ObtenerUsuarios]`, etc.) siguen
  detectándose con su nombre correcto.
- Ausencia de falsos negativos conocidos: ningún SP que hoy se detecta
  correctamente por nombre (Camino A) deja de detectarse.
- `git diff` limitado exactamente a `analyzer/extract.py` + el fixture/tests
  nuevos — nada de `classification.py`, `pipeline.py`, `decompile.py`.

## 10. Puntos que requieren decisión antes de implementar

1. **Alcance del forward-scan**: ¿usar `_find_method_end()` tal cual (límite
   de 500 líneas) o un límite más corto específico para esta búsqueda? Dado
   que `_extract_parameters`/`_extract_result_columns` ya usan ese mismo
   límite para ligar evidencia a una variable de comando, propongo
   reutilizarlo sin crear un segundo límite — pero es una decisión explícita
   a confirmar, no asumida.
2. **Qué hacer con el "posible mejora colateral" de la sección 7** (SPs con
   `CommandType` muy lejos de su propio trigger, hoy no detectados): ¿se
   documenta como hallazgo nuevo si aparece, o se decide de antemano si se
   corrige en el mismo incremento o se deja para uno posterior?
3. **Migración de datos históricos** (sección 8): confirmar que se trata
   como Incremento 5 separado y no se mezcla con el commit de Incremento 4.

## 11. Resultado de la validación (Incremento 4 cerrado)

Implementado exactamente según lo aprobado en las secciones 1-9. Validación
completa ejecutada antes del commit:

- **Tests nuevos**: 15/15 pasando (los 8 originales + A-E obligatorios + 1
  caso adicional descubierto durante la validación, `AnonymousConnectionMergedWithSpNameText`
  — ver más abajo).
- **Suite completa**: 110 passed (95 previos + 15 nuevos), cero regresiones.
- **`AFL_DataCenter`** (re-escaneado contra su `decompiled/` ya existente, sin
  re-decompilar, sin tocar la BD): el conjunto de triggers detectados
  permaneció **459/459 idéntico** — el cambio afecta únicamente
  clasificación, nunca detección. 18 falsos positivos corregidos (10
  `SqlConnection`, 7 `CommandText` con SELECTs de Oracle contaminados). 0
  `SqlConnection`/`OracleConnection` clasificados como SP en el resultado
  final. Los 22 SPs reales conocidos siguen detectándose sin excepción. 0
  SPs nuevos descubiertos por el mayor alcance del forward-scan en los datos
  reales disponibles (medido explícitamente, no asumido).
- **Hallazgo adicional, corregido dentro del alcance aprobado** (no requirió
  cambiar el principio "misma variable = misma evidencia"): un
  `using (new SqlConnection(...))` anónimo sin `;` propia, fusionado por
  `_capture_statement` con la construcción de texto de un SP real en la
  siguiente línea (patrón real de `btnSO_Click`), resolvía por Camino A
  (nombre limpio) independientemente de `command_type_is_sp`. Se cerró con
  una exclusión absoluta por `kind` (`CONNECTION_KINDS`) que bloquea
  cualquier camino hacia `stored_procedure` para `SqlConnection`/
  `OracleConnection`, sin afectar `oracle_package_call` ni detección de
  tabla para esos mismos kinds.
- **Gate sobre datos históricos** (solo lectura, 20 apps con al menos 1
  finding `category='stored_procedure'` en producción — superset de las
  16/204 originales): **380 findings históricos evaluados, 209 serían
  candidatos a reclasificación** bajo la lógica corregida, **0 filas
  nuevas**, **0 casos ambiguos**, conjunto de triggers detectados idéntico
  en las 20 apps. Los datos históricos **no fueron modificados** — quedan
  para Incremento 5, con la gobernanza ya descrita en la sección 8.
- **BD de producción**: verificada intacta antes y después (70 apps, 4120
  `sql_findings`, sin cambios).
