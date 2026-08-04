# ADR-0002: Application Identity Resolution Policy

## Estado
Aprobado

## Contexto
ADR-0000 define la identidad como la continuidad logica de una
aplicacion, permanente e independiente de sus atributos tecnicos. Falta
resolver como la plataforma reconoce, al recibir un analisis nuevo, si
corresponde a una identidad existente -- sin fusionar nunca dos
identidades por inferencia silenciosa.

## Decision
Todo analisis nuevo se resuelve en uno de tres estados:

- **New** -- ninguna senal relevante de coincidencia. Identidad nueva,
  sin friccion.
- **Resolved** -- la senal de coincidencia supera un umbral de confianza
  alto (ej. source_path normalizado identico). Vinculo automatico a la
  identidad existente, sin pedir confirmacion.
- **Candidate** -- hay alguna senal (mismo name con path distinto,
  atributos tecnicos similares) pero no supera el umbral de Resolved. Se
  crea una **identidad provisional**, completamente funcional, marcada
  como posible correspondencia con una identidad existente, pendiente de
  revision humana. El analisis nunca se bloquea.

Cada resolucion Candidate o Resolved se acompaña de un `confidence_score`
(0-1) que representa **confianza en la resolucion** -- una senal
heuristica para enrutamiento y priorizacion de revision, NUNCA una
probabilidad estadistica formal. No implica calibracion, ni garantiza
tasas de error medibles; es deliberadamente pragmatico.

Confirmar una identidad provisional (Candidate) dispara una **Identity
Consolidation**: el conocimiento curado de ambas identidades se
consolida bajo la identidad sobreviviente; la identidad descartada nunca
se elimina, queda como alias permanente hacia la sobreviviente, para que
ninguna referencia historica se rompa.

## Contrato arquitectonico
Las siguientes reglas son invariantes -- cualquier implementacion futura
de esta politica (heuristicas nuevas, similitud estructural, IA) debe
respetarlas para ser valida, independientemente de como calcule sus
senales:

1. Todo analisis se resuelve en exactamente uno de tres estados: `New`,
   `Resolved`, `Candidate`.
2. `Resolved` solo se declara cuando la evidencia supera un umbral de
   confianza alto vigente en ese momento -- el criterio exacto puede
   evolucionar, la existencia de una barrera alta no.
3. `Candidate` jamas se auto-consolida; requiere confirmacion humana
   explicita.
4. `confidence_score` siempre acompaña una resolucion `Candidate` o
   `Resolved`, como senal de confianza para enrutamiento, nunca como
   probabilidad estadistica.
5. `Identity Consolidation` nunca elimina una identidad descartada; la
   preserva como alias permanente hacia la sobreviviente.
6. Ninguna heuristica de resolucion, presente o futura, puede violar las
   reglas 1-5 para calificar como una implementacion valida de esta
   politica.

## Consecuencias
- Se necesita una bandeja de identidades provisionales pendientes de
  revision -- pequena, no bloquea v0.5, pero indispensable para que la
  politica sea operable.
- Formaliza el Principio 3 (Ante la incertidumbre, preservar evidencia y
  pedir validacion humana) de `ARCHITECTURAL_PRINCIPLES.md`.
