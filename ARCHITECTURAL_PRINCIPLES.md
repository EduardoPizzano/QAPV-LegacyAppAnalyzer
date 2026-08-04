# Principios Arquitectónicos

Este documento consolida los principios que rigen decisiones de arquitectura en QAPV-LegacyAppAnalyzer / plataforma de migración MES. Cada principio nace de una decisión concreta (ver ADR referenciado) pero se nombra aquí de forma independiente para que futuras decisiones puedan invocarlo sin repetir su justificación completa.

Creado el 2026-08-04 como parte de la sincronización documental posterior a la auditoría ARB del mismo día (ver `AUDIT-ARB-2026-08-04.md`, hallazgo F3: estos principios ya se habían acordado explícitamente en conversación, pero nunca se habían persistido en un documento propio).

---

## Principio 1 — Datos regenerables vs. conocimiento curado

**Enunciado**: *Los datos regenerables se reemplazan sin miedo en cada análisis. El conocimiento curado se ata siempre a la identidad estable de la aplicación, nunca a un artefacto transitorio de una corrida de análisis específica.*

**Definición de términos**:
- **Datos regenerables**: producidos deterministamente por el pipeline de análisis estático o la introspección de base de datos (`settings`, `sql_findings`, `io_findings`, `security_flags`, `db_procedures`, `db_tables`). Borrarlos y regenerarlos en cada re-análisis es su ciclo de vida correcto — nunca una pérdida real.
- **Conocimiento curado**: producto de juicio humano o asistido por IA sobre esos datos (`review_notes`, `findings`, y lo que se sume en v0.5+: evidencia del checklist de migración, reglas de negocio estructuradas, casos de uso). No se puede regenerar determinísticamente — cada pérdida es una pérdida real de trabajo/entendimiento.

**Origen**: identificado durante la revisión de arquitectura previa a v0.5, al notar que `review_status`/`review_notes` ya necesitaban un tratamiento especial de preservación que ninguna otra tabla tenía.

**Decisiones que se apoyan en este principio**: ADR-0001 (preservar la fila de `apps` en vez de recrearla en cada re-análisis).

---

## Principio 2 — Identidad estable, atributos técnicos mutables

**Enunciado**: *La identidad de una aplicación es un concepto de negocio permanente, independiente de cualquier atributo técnico (nombre, ruta, versión, hash del binario). Los atributos técnicos pueden cambiar libremente sin afectar la identidad.*

**Por qué existe como principio propio**: es el razonamiento que sostiene a ADR-0000, pero — a diferencia de los Principios 1 y 3 — nunca se había nombrado como principio independiente antes de esta sincronización documental (ver `AUDIT-ARB-2026-08-04.md`, sección "Principios Arquitectónicos"). Se formaliza aquí para que decisiones futuras sobre identidad (de aplicaciones, o de cualquier otra entidad del dominio que enfrente el mismo problema) puedan invocarlo directamente.

**Decisiones que se apoyan en este principio**: ADR-0000 (identidad canónica de una aplicación).

---

## Principio 3 — Ante la incertidumbre, preservar evidencia y pedir validación humana

**Enunciado**: *Ante la incertidumbre, la plataforma debe preservar evidencia y solicitar validación humana — nunca modificar conocimiento consolidado mediante inferencias silenciosas.*

**Relación con la disciplina ya existente en el código**: generaliza, a nivel de identidad y de conocimiento curado, la misma disciplina que `analyzer/db_introspect.py` ya aplicaba a nivel de extracción de esquema — devolver `None` (marcar "no se pudo determinar") en vez de adivinar columnas de resultado de un Stored Procedure. Es el mismo principio, aplicado por primera vez explícitamente fuera del código de extracción.

**Decisiones que se apoyan en este principio**: ADR-0002 (política de resolución de identidad: estados `New`/`Resolved`/`Candidate` + `confidence_score`, nunca fusión automática sin evidencia fuerte o confirmación humana).

---

## Principio 4 — Evolución progresiva de infraestructura basada en evidencia

**Enunciado**: *La complejidad tecnológica únicamente deberá incrementarse cuando exista evidencia objetiva de que la nueva capacidad aporta mayor valor que el costo operativo, administrativo y de mantenimiento asociado.*

**Relación con la regla 0.6 de `VISION.md`**: generaliza, a nivel de infraestructura, el mismo criterio que 0.6 ya aplicaba al roadmap de capacidades de negocio (retorno inmediato, no construir por adelantado lo que no se necesita todavía) — ahora nombrado como principio permanente, aplicable a cualquier decisión de infraestructura futura, no solo a la de almacenamiento.

**Origen**: incorporado el 2026-08-04 al revisar ADR-0003, para evitar que ese ADR fijara una tecnología específica (PostgreSQL) como si fuera parte de la decisión arquitectónica permanente, en vez de la implementación vigente de una categoría de solución.

**Decisiones que se apoyan en este principio**: ADR-0003 (política de evolución del almacenamiento — migrar de categoría de motor solo cuando la evidencia lo indique, evaluado mediante el proceso de gobernanza, nunca de forma anticipada ni automática).

---

## Cómo usar este documento

Al proponer una decisión arquitectónica nueva (un ADR), verificar primero si algún principio de este documento ya la respalda — citarlo explícitamente en la sección "Contexto" o "Decisión" del ADR. Si la decisión requiere un principio que no existe aquí todavía, es una señal de que probablemente hace falta nombrar uno nuevo, no solo argumentar la decisión de forma aislada (ver `AUDIT-ARB-2026-08-04.md` para el costo de no hacerlo: los tres principios de este documento existieron como razonamiento disperso antes de consolidarse aquí).
