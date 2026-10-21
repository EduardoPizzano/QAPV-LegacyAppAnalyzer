"""Extraction-only anchor for runtime-dynamic translation strings.

Never imported or executed. Several values are translated via `_(value)`
where `value` comes from the database or a Python constant at runtime (a
bucket label, a severity, a pattern-catalog category, a data-flow role) --
pybabel's static scanner can only find `_("literal string")` calls, so it
can never see these. Without a permanent source reference, `pybabel update`
marks them "obsolete" (commented out with #~) on every re-extraction and
`pybabel compile` then drops them from the .mo entirely, silently breaking
the translation even though the msgid/msgstr pair still sits in the .po
file. Listing every one of them here as a literal `_()` call gives each a
permanent `#:` reference so they survive every future extract/update.

Keep this in sync BY HAND with the actual runtime vocabulary:
- analyzer/db.py: _bucket_by_percentile() buckets ("Baja"/"Media"/"Alta"),
  PATTERN_CATEGORIES keys, FINDING_SEVERITIES
- app.py: DATA_FLOW_ROLE_ORDER, rendered as role.replace('_', ' ') in
  templates/data_flow.html; review_status literal 'obsoleta'
- analyzer/priority_report.py: REVIEW_STATUS_LABELS, _pending_factors()
- analyzer/db.py: group_apps_for_sidebar() floor_section "label_key" values
  ("Operativas en Piso" / "Resto del Portafolio"), rendered via _(item.label_key)
  in templates/library_base.html
"""

from .i18n import _


def _runtime_vocabulary():  # pragma: no cover -- never called, extraction-only
    _("Baja")
    _("Media")
    _("Alta")
    _("critica")
    _("alta")
    _("media")
    _("info")
    _("Sin categorizar")
    _("Inyeccion SQL / concatenacion sin parametros")
    _("Credenciales en texto plano / hardcodeadas")
    _("Manejo de errores silencioso")
    _("Riesgo de caida por timer/thread sin manejo de excepciones")
    _("Codigo muerto / nunca invocado")
    _("Falta de transaccion / atomicidad")
    _("Certificacion de operador nunca validada")
    _("Falso exito / perdida silenciosa de datos")
    _("Autenticacion debil / sin re-verificar permisos")
    _("Contaminacion de decompilacion (proyecto ajeno)")
    _("Bypass de autorizacion / control de permisos ausente")
    _("productor numerico")
    _("productor passfail")
    _("consumidor medicion")
    _("consumidor resultado")
    _("consumidor general")
    _("mixto")
    _("Sin revision de logica de negocio todavia")
    _("Marcada como lista para migrar")
    _("Desconocido")
    _("obsoleta")
    _("Criticidad operacional")
    _("Operativas en Piso")
    _("Resto del Portafolio")
