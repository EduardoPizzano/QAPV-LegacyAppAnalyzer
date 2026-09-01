# Metodología: cómo generar y marcar la revisión de lógica de negocio de una app

Este documento existe para no perder el hilo del proceso manual (asistido) de revisión
de lógica de negocio entre apps y entre sesiones. No es una funcionalidad del código —
es el procedimiento que un humano (o Claude, guiado por un humano) debe seguir cada vez
que se revisa una app, y el criterio para decidir qué valor de `review_status` le
corresponde. Se escribe aquí porque `review_status`/`review_notes` (`analyzer/db.py`) son
campos de texto libre: nada en el código impone este procedimiento, así que solo
sobrevive si queda documentado.

> Nota de contexto arquitectónico: `VISION.md` §8 ya señala que este campo de 3-4 valores
> manuales es un diseño transitorio, candidato a reemplazarse por un cálculo verificable
> más adelante. Mientras eso no se implemente, este es el procedimiento vigente y la
> única fuente de verdad sobre "qué tan revisada" está una app.

## 1. Cuándo aplica

Después de que una app ya fue analizada automáticamente (existe su fila en `apps`, con
`sql_findings`/`settings`/`io_findings` poblados) y **antes** de considerarla lista para
migrar a Ignition MES. El análisis automático (extracción de SQL, settings, stack
tecnológico) nunca es suficiente por sí solo — captura *qué texto SQL existe en el
código*, no *qué regla de negocio real implementa ese código* (cuándo se ejecuta, contra
qué otra validación compite, qué pasa si falla, qué IDs hardcodeados usa).

## 2. Paso a paso para generar el contenido de la revisión

Esto es lo que se hizo para `DataTransfer v2.49/Release` (2026-08-24) y debe repetirse
igual para cualquier otra app:

1. **Ubicar la app**: `app_id`, `source_path`, y la carpeta decompilada real en
   `decompiled/<raiz>/...` (la raíz es el primer segmento del `name` de la app, ej.
   `DataTransfer v2.49` para `DataTransfer v2.49/Release`).
2. **Traer lo ya extraído, nunca releer el `.cs` completo a ciegas**: consultar
   directamente `qapv_analyzer.db` (`sql_findings`, `settings`, `io_findings` filtrados
   por `app_id`) y agrupar por `class_name`+`method`. Esto ya trae la mayoría del SQL con
   conexión resuelta. Para apps grandes (miles de líneas), cargar el archivo completo en
   contexto es un desperdicio — se hace vía un script Python de una vez (ej.
   `sqlite3`+`json`) que arma un Markdown intermedio agrupado por método, y ESE es el que
   se lee.
3. **Reconstruir el SQL concatenado real cuando `resolved` es `null`**: `raw` suele traer
   la llamada completa a `new SqlCommand("..." + var + "...")` — reconstruir el texto con
   `{var}` en el lugar exacto de cada tramo no-literal (nunca aplanar a un resumen).
4. **Identificar los métodos sin SQL capturado** (aparecen como `{cmdText}`, `{text2}`, o
   sin ninguna fila de SQL pese a tener una conexión) — estos son los que construyen el
   comando con `StringBuilder`, ternarios anidados, o `switch` sobre una variable de
   equipo/tipo. El extractor automático (`analyzer/extract.py`) no los reconoce por
   diseño (evita adivinar) — **requieren lectura manual línea a línea** del `.cs`
   decompilado real (usar `grep`/`Grep` para ubicar la línea de la firma del método, luego
   `Read` con `offset`/`limit` acotado al cuerpo del método, nunca el archivo entero).
5. **Buscar la lógica de orquestación, no solo el CRUD aislado**: el hallazgo de más valor
   real no suele estar en una sola consulta, sino en el método que **decide cuál de varias
   consultas/validadores correr** y **qué hacer cuando dos compiten** (ej. "gana el de
   fecha más reciente", "un rework en dispositivo X solo bloquea si el equipo activo es
   Y"). Buscar deliberadamente estos métodos "enrutadores" — suelen no tocar SQL
   directamente, así que ni siquiera aparecen en `sql_findings`.
6. **Producir dos artefactos, no uno**:
   - Un documento completo (archivo aparte, entregado al usuario vía chat/archivo) con
     el SQL exacto de cada método, sin resumir — es la referencia técnica completa.
   - Un resumen condensado (10-20 líneas, en español, sin jerga de columnas de BD) para
     el campo `review_notes` — pensado para que un humano lo lea en 30 segundos antes de
     aprobar. Debe cubrir: qué hace la app, las 3-6 reglas de negocio no obvias que se
     descubrieron, y las limitaciones que quedan abiertas.
7. **Guardar con `db.set_review(app_id, status, notes)`** — la misma función que usa el
   botón "Guardar revisión" de la UI. Puede llamarse directamente desde un script Python
   (`from analyzer import db; db.set_review(...)`) sin pasar por el servidor Flask; es
   solo lectura/escritura sobre SQLite, no requiere el servidor corriendo.
8. **Nunca tocar el `review_status` sin que el criterio de la sección 3 lo justifique
   explícitamente** — completar las notas no implica automáticamente que el estado deba
   subir; son dos decisiones separadas.

## 3. Cómo decidir `review_status`

Valores reales soportados por el código (`analyzer/db.py::REVIEW_STATUSES`):
`borrador` / `logica_revisada` / `listo_para_migrar` / `obsoleta`.

| Estado | Criterio para asignarlo |
|---|---|
| `borrador` | Estado por defecto. Solo existe la extracción automática (SQL/settings crudos); nadie leyó el código a mano todavía. Ninguna app nace en otro estado. |
| `logica_revisada` | Un humano (con o sin ayuda de Claude) leyó a mano **todo** el código que toca SQL, incluidos los métodos que el extractor automático no resolvió (paso 4 arriba), y documentó en `review_notes` las reglas de negocio reales (no solo qué tablas toca). Puede seguir habiendo límites conocidos y explícitos (ej. "no se exploró el subsistema de reportes") — eso no bloquea este estado, siempre que estén escritos, no escondidos. |
| `listo_para_migrar` | Además de `logica_revisada`: (a) no quedan preguntas HYPOTHESIS/UNKNOWN bloqueantes sobre el comportamiento crítico (ej. cuál validador gana, qué bloquea qué) — si algo sigue siendo hipótesis, no calza aquí todavía; (b) un dueño de proceso de planta confirmó que la reconstrucción coincide con el proceso real (no basta con que el código "se entienda", alguien de negocio lo validó); (c) no hay Hallazgos abiertos de severidad alta/crítica para esa app en el módulo de Hallazgos. Este es el único estado que debería habilitar empezar a diseñar el reemplazo en Ignition MES. |
| `obsoleta` | Regla explícita ya usada en este proyecto: una app cuya **única** conexión a base de datos apunta a un servidor decomisionado (ej. `NAAMRT-QCS11`) se marca `obsoleta`, nunca `logica_revisada` — no tiene sentido documentar reglas de negocio de un sistema que ya no puede operar. También aplica si el negocio confirma explícitamente que el proceso fue reemplazado por otra app ya identificada en el portafolio. **Esta regla vive únicamente en este documento y en la memoria de la sesión — no hay ningún código que la detecte automáticamente**, así que debe aplicarse a mano cada vez.

## 4. Casos especiales (qué hacer, para no reinventarlo cada vez)

### La app usa Stored Procedures
`sql_findings.is_stored_procedure = 1` marca cada llamada a un SP. Para que la revisión
cuente como `logica_revisada`, las notas deben documentar, por cada SP relevante: nombre
completo (`schema.objeto`), qué parámetros recibe (`sql_findings.parameters` cuando
existe), y qué hace en términos de negocio — no basta con listar que "se llama a
`dbo.ActualizaJob`". Si el pipeline logró conectarse en solo-lectura al servidor real
durante el análisis (`enrich.py`/`db_introspect.py`), el cuerpo real del SP ya vive en
`db_procedures.definition` — **leer de ahí primero** antes de intentar inferir el
comportamiento solo por cómo se le llama desde C#, porque el cuerpo real puede hacer más
de lo que el nombre sugiere.

### La app no tiene ninguna conexión a base de datos
Que `sql_findings` esté vacío y `apps.db_drivers` no liste ningún driver **no es
suficiente para saltarse la revisión** — hay que confirmarlo como FACT (no asumirlo) y
documentar en las notas qué hace la app en su lugar: I/O de archivos (`io_findings`),
automatización de UI, comunicación con hardware/puerto serie, generación de reportes
locales, etc. Una app "sin base de datos" igual puede llegar a `logica_revisada` — el
criterio de la sección 3 no exige que exista SQL, exige que la lógica real (cualquiera
que sea) esté entendida y escrita.

### La app comparte código/Artifact con otras (ver ADR-0004)
Si la app es un `Artifact` técnicamente idéntico o derivado de otra ya revisada
(`artifact_relationships` con `relationship_type='identical'` o `binary_hash` igual, ver
ADR-0004), no se copia ciegamente el `review_status` de la otra — cada `Deployment`
(fila `apps`) se revisa por separado, porque puede estar configurado distinto
(`settings` distintos, ver ejemplo real: `DataTransfer v2.49/Release` vs
`DataTransfer v2.49/app.publish`, mismo `.exe` fuente pero deployments no verificados
como idénticos). Sí se puede **reusar el documento técnico completo** entre ambos si se
confirma que el binario es el mismo — pero el acto de marcar `review_status` es por
Deployment, nunca automático entre Deployments del mismo Artifact.

## 5. Plantilla de notas condensadas (para consistencia entre apps)

```
RESUMEN DE LOGICA DE NEGOCIO -- <Nombre de la app>
(condensado para revision humana; el detalle completo con SQL exacto por metodo esta en <ruta/nombre del documento completo, si existe>)

QUE ES: <1-2 lineas, el proposito real de la app en el proceso de planta>

CONEXIONES: <servidor/BD por variable de conexion relevante>

REGLAS DE NEGOCIO CLAVE (no obvias, confirmadas leyendo el codigo):
1. <regla de orquestacion/decision>
2. <regla de bloqueo/dependencia entre modulos>
3. <cualquier caso especial de cliente/linea/equipo hardcodeado>
...

LIMITACIONES: <que se dejo sin revisar explicitamente, y por que>
```

## 6. Ejemplo de referencia

`DataTransfer v2.49/Release` (`app_id=432`, revisado 2026-08-24) es el caso de
referencia completo de este procedimiento: documento técnico completo con 132 métodos y
SQL exacto, más el motor de decisión (`ValidaGEO`/`ValidaIL`), la regla de bloqueo
cruzado por retrabajo, y el mecanismo de auto-generación de retrabajo
(`PuntaPreviaOK`) — ninguno de estos tres últimos hallazgos aparecía en `sql_findings`
porque no tocan SQL directamente, se encontraron siguiendo el paso 5 de la sección 2.
