# ADR-0001: Preservar la fila de `apps` (identity_id estable) a traves de re-analisis

## Estado
Aprobado (2026-08-04, alineado con ADR-0000/ADR-0002)

Se apoya en el Principio 1 (Datos regenerables vs. conocimiento curado) de
`ARCHITECTURAL_PRINCIPLES.md`.

## Contexto
`save_analysis()` re-analiza una app mediante DELETE + INSERT de su fila
en `apps`, asignando un `id` autoincremental nuevo cada vez. Todo hijo con
ON DELETE CASCADE se pierde salvo que se preserve manualmente (hoy:
review_status/review_notes) o se diseñe sin FK (hoy: findings,
referenciado por app_name). ADR-0000 establecio que la identidad real de
una app es un identificador estable (`identity_id`), independiente de
name/source_path; ADR-0002 establecio como se resuelve ese identity_id
para un analisis nuevo (New/Resolved/Candidate). Este ADR resuelve que
pasa, dado un identity_id ya resuelto, con la fila y sus datos.

## Decision
`save_analysis()` deja de recrear la fila `apps` en cada re-analisis:
- La resolucion de identity_id (ADR-0002) ocurre ANTES de persistir.
- Si el analisis resuelve a un identity_id existente (`Resolved`, o un
  `Candidate` ya confirmado via Identity Consolidation), se hace UPDATE
  sobre la fila `apps` existente -- el `id` interno nunca cambia.
- Si resuelve a `New` (o `Candidate` sin confirmar), se hace INSERT de
  una fila nueva con un identity_id nuevo.
- Las tablas hijas verdaderamente regenerables (settings, sql_findings,
  io_findings, security_flags, db_procedures, db_tables) conservan su
  comportamiento actual de DELETE + INSERT por app_id -- son
  correctamente regenerables en cada analisis, sin cambios.
- `findings` no se modifica en este ADR (sigue por app_name) -- su
  convergencia a identity_id queda anotada en ADR-0000 como trabajo
  futuro, no bloqueante.

Nota: la version original de este ADR proponia `UNIQUE(name)` como
mecanismo de upsert. Se descarta explicitamente tras ADR-0000/ADR-0002 --
`name` nunca es la clave de identidad ni de upsert; el mecanismo real es
la resolucion de identity_id.

## Consecuencias
- Cualquier tabla nueva de contenido curado que se agregue en v0.5+ queda
  protegida contra perdida por re-analisis automaticamente, sin logica de
  preservacion propia, siempre que se referencie por identity_id.
- No corrige el bug ya conocido de colision de nombres en el
  descubrimiento por lote -- ahora ese escenario se resuelve, por diseno,
  como un `Candidate` (ADR-0002), nunca como una sobreescritura
  silenciosa.
- El cambio es solo hacia adelante: identity_ids/ids ya generados no se
  reescriben retroactivamente.

## Riesgos
- Este ADR depende operacionalmente de ADR-0002: la resolucion de
  identity_id debe existir antes de que save_analysis() pueda apoyarse
  en ella.
- Verificar que no exista codigo que asuma implicitamente que el `id` de
  una app cambia en cada re-analisis.
