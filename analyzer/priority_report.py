"""Capa de presentacion/explicabilidad sobre el Priority & Complexity Engine
(analyzer/db.py: get_priority_and_complexity()).

Este modulo NO recalcula score, buckets, pesos ni factores -- el algoritmo
completo sigue viviendo, sin cambios, en analyzer/db.py. Todo lo de aqui es
una interpretacion/narracion de esa salida ya calculada, mas una seccion de
observaciones arquitectonicas derivada de datos ya existentes
(companion_assemblies, catalogo de patrones) -- nada de esto afecta la
prioridad ni el score de ninguna app."""

import re
from collections import defaultdict

from . import db

FACTOR_LABELS = {
    "complejidad_tecnica": "Complejidad tecnica",
    "riesgo": "Riesgo",
    "dependencias": "Dependencias",
    "reglas_negocio": "Reglas de negocio",
    "complejidad_integracion": "Complejidad de integracion",
    "reutilizacion_potencial": "Reutilizacion potencial",
}

REVIEW_STATUS_LABELS = {
    "borrador": "Sin revision de logica de negocio todavia",
    "logica_revisada": "Logica de negocio revisada",
    "listo_para_migrar": "Marcada como lista para migrar",
}

# Traduce el bucket YA decidido por el motor a una frase accionable -- no es
# un calculo nuevo, es vocabulario fijo indexado por (factor, bucket).
_INTERPRETATIONS = {
    ("complejidad_tecnica", "Alta"): "Superficie de datos grande: muchas consultas/SP/tablas que reconstruir.",
    ("complejidad_tecnica", "Media"): "Superficie de datos moderada.",
    ("complejidad_tecnica", "Baja"): "Superficie de datos pequena: poco que reconstruir en la capa de datos.",
    ("riesgo", "Alta"): "Concentra hallazgos de severidad alta/critica -- revisar antes de comprometer fecha de migracion.",
    ("riesgo", "Media"): "Riesgo moderado, sin señales de severidad critica dominante.",
    ("riesgo", "Baja"): "Sin hallazgos de riesgo relevantes detectados hasta ahora.",
    ("dependencias", "Alta"): "Muy interconectada: conviene migrarla junto con las apps que comparten sus recursos, no de forma aislada.",
    ("dependencias", "Media"): "Comparte algunos recursos con otras apps -- coordinar el orden de migracion.",
    ("dependencias", "Baja"): "Poco o nada interconectada -- puede migrarse de forma independiente.",
    ("reglas_negocio", "Alta"): "Mucho volumen documentado de logica de negocio -- probable esfuerzo funcional alto.",
    ("reglas_negocio", "Media"): "Volumen medio de logica de negocio documentada.",
    ("reglas_negocio", "Baja"): "Poco volumen de logica de negocio documentada hasta ahora.",
    ("complejidad_integracion", "Alta"): "Depende de varias integraciones externas -- replicarlas es parte critica del esfuerzo.",
    ("complejidad_integracion", "Media"): "Algunas integraciones externas a replicar.",
    ("complejidad_integracion", "Baja"): "Pocas o ninguna integracion externa detectada.",
    ("reutilizacion_potencial", "Alta"): "Comparte varios patrones con otras apps -- una solucion aqui es reutilizable en el resto del portafolio.",
    ("reutilizacion_potencial", "Media"): "Comparte algunos patrones con otras apps.",
    ("reutilizacion_potencial", "Baja"): "Poco o nada de conocimiento reutilizable detectado con otras apps todavia.",
}

_SEVERITY_PATTERN = re.compile(r"\[(critica|alta|media|info)\]")
_LEADING_NUMBER = re.compile(r"^(\d+)")


def short_evidence(key: str, factor: dict) -> str:
    """Resume en una linea la evidencia que YA calculo el motor -- nunca
    inventa un numero nuevo, solo lee/cuenta lo que ya viene en 'evidence'.
    Un factor sin_evidencia (VISION.md 0.3: nunca inferir en silencio) se
    reporta explicitamente como tal, nunca como un 0."""
    if factor.get("sin_evidencia"):
        return "Sin evidencia disponible"

    evidence = factor.get("evidence") or []

    if key == "complejidad_tecnica":
        nums = [int(m.group(1)) for e in evidence if (m := _LEADING_NUMBER.match(e))]
        if len(nums) >= 2:
            return f"{nums[0]} consulta(s)/SP, {nums[1]} tabla(s) introspectada(s)"
        return "0 consultas/SP, 0 tablas"

    if key == "riesgo":
        if not evidence:
            return "Sin hallazgos ni flags de seguridad detectados"
        criticos = sum(1 for e in evidence if _SEVERITY_PATTERN.search(e) and _SEVERITY_PATTERN.search(e).group(1) == "critica")
        texto = f"{len(evidence)} hallazgo(s)/flag(s)"
        if criticos:
            texto += f" ({criticos} critico(s))"
        return texto

    if key == "dependencias":
        if not evidence:
            return "No comparte tablas/SP ni servidor con otras apps"
        return f"comparte {len(evidence)} recurso(s) (tabla/SP/servidor) con otras apps"

    if key == "reglas_negocio":
        if not evidence:
            return "0 lineas documentadas"
        n = factor.get("raw")
        return f"{n} linea(s) documentadas (proxy)" if n is not None else evidence[0]

    if key == "complejidad_integracion":
        if not evidence:
            return "Sin integraciones externas detectadas"
        return ", ".join(evidence)

    if key == "reutilizacion_potencial":
        if not evidence:
            return "No comparte patrones conocidos con otras apps"
        return f"comparte {len(evidence)} patron(es) con otras apps"

    return "; ".join(evidence[:2]) if evidence else "Sin evidencia disponible"


def _interpretation(key: str, factor: dict) -> str:
    bucket = factor.get("bucket")
    if bucket is None:
        return "No se puede interpretar todavia -- falta evidencia (ver columna Evidencia)."
    return _INTERPRETATIONS.get((key, bucket), f"Bucket calculado: {bucket}.")


def _readiness_label(review_status: str | None) -> str:
    return REVIEW_STATUS_LABELS.get(review_status, review_status or "Desconocido")


def _pending_factors(row: dict) -> list[str]:
    pending = [FACTOR_LABELS[k] for k, f in row["factors"].items() if f.get("sin_evidencia")]
    pending += ["Cantidad de usuarios", "Criticidad operacional"]
    return pending


def _executive_summary(row: dict, review_status: str | None) -> dict:
    """Resumen ejecutivo: NO recalcula nada, solo reordena/traduce campos que
    get_priority_and_complexity() ya produjo (mas review_status, ya
    existente en la tabla apps) para consumo rapido de un lector no tecnico."""
    return {
        "prioridad": row["prioridad"],
        "complejidad_tecnica": row["factors"]["complejidad_tecnica"]["bucket"],
        "complejidad_integracion": row["factors"]["complejidad_integracion"]["bucket"],
        "riesgo": row["factors"]["riesgo"]["bucket"],
        "estado_preparacion": _readiness_label(review_status),
        "factores_pendientes": _pending_factors(row),
    }


def _narrative(row: dict) -> str:
    """Construye la narrativa exclusivamente con los campos ya calculados
    por el motor (priority_score_breakdown + evidence de cada factor) -- no
    hay texto generico: cada oracion cita un numero o una evidencia real."""
    breakdown = sorted(row["priority_score_breakdown"], key=lambda b: -abs(b["aporte"]))
    top = [b for b in breakdown if b["aporte"] != 0][:2]

    parts = [f"Esta app quedo con prioridad **{row['prioridad']}** (score {row['priority_score']})."]

    if top:
        frases = []
        for b in top:
            label = FACTOR_LABELS[b["factor"]]
            direccion = "subio" if b["aporte"] > 0 else "bajo"
            frases.append(f"{label} en nivel {b['bucket']} ({direccion} el score en {abs(b['aporte'])})")
        parts.append("El/los factor(es) con mayor influencia en el score fueron " + " y ".join(frases) + ".")

        ev = row["factors"][top[0]["factor"]]["evidence"]
        if ev:
            extra = f" (y {len(ev) - 1} evidencia(s) mas, ver tabla de factores abajo)" if len(ev) > 1 else ""
            parts.append(f"Evidencia concreta detras de ese factor: {ev[0]}{extra}.")
    else:
        parts.append("Ningun factor tuvo un aporte distinto de cero para esta app -- valores parejos frente al resto del portafolio.")

    pendientes = _pending_factors(row)
    if pendientes:
        parts.append("Informacion que aun falta para completar la evaluacion: " + ", ".join(pendientes) + ".")

    return " ".join(parts)


def _conclusion(row: dict, review_status: str | None) -> str:
    """Conclusion ejecutiva -- no repite la tabla, sintetiza que haria un
    arquitecto con esta informacion. Cada frase se deriva de un bucket ya
    calculado, no de una regla nueva."""
    f = row["factors"]

    riesgos = []
    if f["riesgo"]["bucket"] == "Alta":
        riesgos.append("el riesgo (hallazgos/flags de seguridad) es alto y deberia revisarse antes de comprometer fecha de migracion")
    if f["dependencias"]["bucket"] == "Alta":
        riesgos.append("esta muy interconectada con otras apps, lo que puede forzar una migracion coordinada en vez de aislada")
    if not riesgos:
        riesgos.append("no se detectaron riesgos dominantes con la evidencia disponible hoy")

    fortalezas = []
    if f["reutilizacion_potencial"]["bucket"] == "Alta":
        fortalezas.append("comparte patrones conocidos con otras apps, lo que abarata construir una solucion reutilizable")
    if f["complejidad_tecnica"]["bucket"] == "Baja":
        fortalezas.append("su superficie tecnica de datos es pequena")
    if f["complejidad_integracion"]["bucket"] == "Baja":
        fortalezas.append("no depende de integraciones externas complejas")
    if not fortalezas:
        fortalezas.append("ninguna fortaleza clara segun los factores calculados hoy")

    pendientes = _pending_factors(row)

    return (
        f"Prioridad recomendada: {row['prioridad']}. "
        f"Principal(es) riesgo(s): {'; '.join(riesgos)}. "
        f"Principal(es) fortaleza(s): {'; '.join(fortalezas)}. "
        f"Pendiente de evaluar antes de decidir: {', '.join(pendientes)}. "
        f"Estado de revision de logica de negocio: {_readiness_label(review_status)}."
    )


def _all_apps_meta() -> list[dict]:
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT id, name, companion_assemblies FROM apps")]


def _companion_assembly_observations(app_id: int, app_name: str, all_apps_meta: list[dict]) -> list[dict]:
    """Observacion arquitectonica derivada de un dato ya existente
    (apps.companion_assemblies): que otras apps reutilizan el mismo
    ensamblado companion que esta. Nunca afecta el score -- es informativa,
    y se marca explicitamente como inferencia (nombre de ensamblado
    compartido no es lo mismo que "mismo subsistema confirmado")."""
    mine_row = next((a for a in all_apps_meta if a["id"] == app_id), None)
    if not mine_row or not mine_row["companion_assemblies"]:
        return []
    mine = {n.strip() for n in mine_row["companion_assemblies"].split(",") if n.strip()}
    if not mine:
        return []

    shared: dict[str, set[str]] = defaultdict(set)
    for a in all_apps_meta:
        if a["id"] == app_id or not a["companion_assemblies"]:
            continue
        others = {n.strip() for n in a["companion_assemblies"].split(",") if n.strip()}
        for name in mine & others:
            shared[name].add(a["name"])

    observations = []
    for name, apps in sorted(shared.items()):
        observations.append({
            "texto": f"'{app_name}' reutiliza el ensamblado '{name}', tambien encontrado en: {', '.join(sorted(apps))}.",
            "es_inferencia": True,
            "nota": "Inferencia basada en el nombre del ensamblado compartido -- no confirma que ambas apps pertenezcan al mismo subsistema de negocio; verificar manualmente.",
        })
    return observations


def _pattern_cross_reference(row: dict) -> list[dict]:
    """Reexpone (no recalcula) la evidencia ya producida por los factores
    'reutilizacion_potencial' y 'dependencias' como observaciones
    arquitectonicas -- mismo dato, presentado como relacion entre apps en
    vez de como aporte al score."""
    obs = []
    reutil = row["factors"]["reutilizacion_potencial"]
    if reutil.get("evidence"):
        extra = f" (+{len(reutil['evidence']) - 1} mas)" if len(reutil["evidence"]) > 1 else ""
        obs.append({
            "texto": f"Comparte patrones del Catalogo de patrones recurrentes con otras apps: {reutil['evidence'][0]}{extra}.",
            "es_inferencia": False,
            "nota": "Dato ya calculado por el factor 'Reutilizacion potencial' -- ver tabla de factores para el detalle completo.",
        })
    dep = row["factors"]["dependencias"]
    if dep.get("evidence"):
        extra = f" (+{len(dep['evidence']) - 1} mas)" if len(dep["evidence"]) > 1 else ""
        obs.append({
            "texto": f"Comparte tabla/SP o servidor con otras apps: {dep['evidence'][0]}{extra}.",
            "es_inferencia": False,
            "nota": "Dato ya calculado por el factor 'Dependencias' -- ver tabla de factores para el detalle completo.",
        })
    return obs


def build_report(app_id: int) -> dict | None:
    """Punto de entrada de este modulo: toma la fila ya calculada por
    get_priority_and_complexity() para esta app y la convierte en un reporte
    de apoyo a la decision (resumen ejecutivo, narrativa, tabla de factores
    con Evidencia/Interpretacion separadas, observaciones arquitectonicas,
    conclusion). Ningun numero se recalcula aqui."""
    rows = db.get_priority_and_complexity()
    row = next((r for r in rows if r["app_id"] == app_id), None)
    if row is None:
        return None

    app_data = db.get_app(app_id)
    review_status = app_data["app"].get("review_status") if app_data else None

    factor_rows = []
    for key in FACTOR_LABELS:
        f = row["factors"][key]
        factor_rows.append({
            "key": key,
            "label": FACTOR_LABELS[key],
            "bucket": f.get("bucket"),
            "raw": f.get("raw"),
            "evidencia_corta": short_evidence(key, f),
            "evidencia_completa": f.get("evidence") or [],
            "interpretacion": _interpretation(key, f),
        })

    all_meta = _all_apps_meta()
    observaciones = _companion_assembly_observations(app_id, row["app_name"], all_meta)
    observaciones += _pattern_cross_reference(row)

    return {
        "app_id": app_id,
        "app_name": row["app_name"],
        "resumen_ejecutivo": _executive_summary(row, review_status),
        "narrativa": _narrative(row),
        "factor_rows": factor_rows,
        "priority_score": row["priority_score"],
        "priority_score_breakdown": row["priority_score_breakdown"],
        "pendiente_negocio": row["pendiente_negocio"],
        "observaciones_arquitectonicas": observaciones,
        "conclusion": _conclusion(row, review_status),
    }
