"""
Reconciliacion forense de 3 conflictos de lineage (sesion 2026-09-10/11).

NO modifica runs 1 ni 2 (quedan intactos como evidencia historica).
Crea una nueva lineage_run (id=3) que copia byte-a-byte los 2098 edges de la
run 2, excepto los 123 edges de los 3 conflictos, que se reconcilian segun
evidencia forense de lectura de codigo decompilado.

Ver conversacion Claude Code 2026-09-11 para el detalle completo de la
investigacion. Este script es de un solo uso (idempotente: aborta si la
run 3 ya existe).
"""
import sqlite3
import json
import datetime

DB_PATH = "qapv_analyzer.db"
SOURCE_RUN_ID = 2
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()
RECONCILED_BY = "Claude Sonnet 5 (Claude Code) + jesuseduardo.herrera@aflglobal.com -- sesion forense 2026-09-10/11"

CONFLICT1_IDS = {3883, 3884, 3885, 3886, 3887, 3888, 3889, 3890, 3891, 3892, 3893, 3894, 3895, 3896}

CONFLICT2_IDS = {
    3378, 3379, 3380, 3381, 3382, 3383, 3384, 3385,
    3394, 3395, 3396, 3397, 3398, 3399, 3400, 3401, 3402,
    3411, 3412, 3413, 3414, 3415, 3416, 3417, 3418, 3419,
    3436, 3437, 3438, 3439, 3440, 3441, 3442, 3443, 3444,
    3445, 3446, 3447, 3448, 3449, 3450, 3451, 3452, 3453,
    3454, 3455, 3456, 3457, 3458, 3459, 3460, 3461, 3462,
    3463, 3464, 3465, 3466, 3467, 3468, 3469, 3470, 3471,
    3472, 3473, 3474, 3475, 3476, 3477, 3478, 3479, 3480,
    3481, 3482, 3483, 3484, 3485, 3486, 3487, 3488, 3489,
    3490, 3491, 3492, 3493, 3494, 3495, 3496, 3497,
    3498, 3499, 3500, 3501, 3502, 3503, 3504, 3505,
}

CONFLICT3_IDS = {3870, 3871, 3872, 3873, 3874, 3875, 3876, 3877, 3878, 3879, 3880, 3881, 3882}

GRABARL1_FILES_CHECKED = [
    r"decompiled\DataTransfer\DataTransfer\DataTransfer.cs",
    r"decompiled\DataTransfer v2.46\Release\DataTransfer\DataTransfer.cs",
    r"decompiled\DataTransfer v2.46\app.publish\DataTransfer\DataTransfer.cs",
    r"decompiled\DataTransfer v2.49\Release\DataTransfer\DataTransfer.cs",
    r"decompiled\DataTransfer v2.49\app.publish\DataTransfer\DataTransfer.cs",
    r"decompiled\Geometria\Release\DataTransfer\DataTransfer.cs",
    r"decompiled\Geometria\app.publish\DataTransfer\DataTransfer.cs",
    r"decompiled\Polaridad\Release\DataTransfer\DataTransfer.cs",
    r"decompiled\Polaridad\app.publish\DataTransfer\DataTransfer.cs",
    r"decompiled\VINS1\VINS1\DataTransfer\DataTransfer.cs",
    r"decompiled\VINS2Plus\VINS1\DataTransfer\DataTransfer.cs",
]

SMARTCHECK_INSERT_WRITE_OP = {
    "operation": "insert",
    "columns": [
        "SERIAL_NUMBER", "WIP_ENTITY_NAME", "ENDS", "CONNECTOR", "STATUS", "TEST_DATE",
        "CABLE", "TASK", "CONNECTOR_TYPE", "FILE_IMAGE",
        "ZONE_A_DEFECTS_FIBER1..24 / ZONE_A_SCRATCHES_FIBER1..24 / "
        "ZONE_B_DEFECTS_FIBER1..24 / ZONE_B_SCRATCHES_FIBER1..24 / CORE_FIBER1..24",
        "SOURCE_PATH", "EMPLOYEE_NUMBER", "DEVICE", "DEVICE_STATUS",
        "CREATION_DATE", "CREATED_BY", "LAST_UPDATE_DATE", "LAST_UPDATED_BY", "EnOracle",
    ],
    "confidence": 90,
    "evidence_source": "InterAFL.ViewModel/ProcVM.cs:8803 (ProcSmartCheck) -- literal 'Insert into "
                        "XXAFL_QAPV_TESTS_SMART_CHECK (...)' leido directamente del codigo decompilado, "
                        "fuente = archivo .xlsx exportado por el instrumento de inspeccion MPO (item.Fuente)",
}


def build_reconciliation_block(conflict_id, old_row, new_confidence, new_label, new_operation, reason, extra=None):
    block = {
        "supersedes": {"run_id": SOURCE_RUN_ID, "edge_id": old_row["id"]},
        "reconciliation_date": NOW,
        "method": "manual_forensic_code_review",
        "reviewed_by": RECONCILED_BY,
        "conflict_id": conflict_id,
        "previous_state": {
            "confidence": old_row["confidence"],
            "confidence_label": old_row["confidence_label"],
            "operation": old_row["operation"],
            "detection_method": old_row["detection_method"],
        },
        "new_state": {
            "confidence": new_confidence,
            "confidence_label": new_label,
            "operation": new_operation,
        },
        "reason": reason,
    }
    if extra:
        block["extra"] = extra
    return block


def reconcile_edge(old_row):
    eid = old_row["id"]
    evidence = json.loads(old_row["evidence_json"]) if old_row["evidence_json"] else {}
    if not isinstance(evidence, dict):
        evidence = {"relations": evidence}

    if eid in CONFLICT1_IDS:
        new_conf, new_label, new_op = 45, "POSSIBLE", old_row["operation"]
        evidence["reconciliation"] = build_reconciliation_block(
            "CONFLICT_1_COPYVIAVI_SMARTCHECK", old_row, new_conf, new_label, new_op,
            reason=(
                "CopyVIAVI no tiene ninguna referencia literal a XXAFL_QAPV_TESTS_SMART_CHECK en "
                "sql_findings. La unica operacion relevante es una llamada a la SP 'InsertVIAVI' "
                "(sql_findings.id=28492, target='InsertVIAVI', is_stored_procedure=1) cuyo cuerpo "
                "nunca pudo introspectarse (db_procedures no tiene esa definicion) porque CopyVIAVI "
                "conecta a NAAMRT-QCS11, servidor decomisionado (ver KNOWN_UNREACHABLE_SERVERS en "
                "analyzer/enrich.py). La relacion original (CONFIRMED/85) parece inferida por "
                "coincidencia de forma de columnas contra otros productores de la misma tabla, no por "
                "evidencia directa de codigo. Se degrada a POSSIBLE/45 (nivel mas bajo ya existente en "
                "el modelo) hasta que el cuerpo de InsertVIAVI pueda introspectarse."
            ),
            extra={"black_box_procedure": "InsertVIAVI", "black_box_server": "NAAMRT-QCS11 (decomisionado)"},
        )
        return new_conf, new_label, new_op, evidence

    if eid in CONFLICT2_IDS:
        new_conf, new_label, new_op = 70, "PROBABLE", old_row["operation"]
        evidence["reconciliation"] = build_reconciliation_block(
            "CONFLICT_2_GRABARL1_DEADCODE", old_row, new_conf, new_label, new_op,
            reason=(
                "El metodo GrabaRL1(...) y su INSERT literal hacia XXAFL_QAPV_TESTS_RL1 SI existen en "
                "el codigo decompilado de esta app (clase DataTransfer, NO parcial -- toda la clase "
                "vive en un unico archivo .cs). Busqueda exhaustiva de 'GrabaRL1(' como llamada (no "
                "declaracion) en los 11 arboles decompilados de toda la familia DataTransfer/Geometria/"
                "Polaridad/VINS1/VINS2Plus: CERO call-sites encontrados en los 11. El codigo es "
                "alcanzable-por-compilacion pero NO ejecutable-en-practica (dead code), probablemente "
                "remanente de copiar la clase completa entre proyectos. Se degrada de CONFIRMED/85 a "
                "PROBABLE/70 (el modelo no distingue 'codigo presente' de 'writer activo'; PROBABLE es "
                "la categoria intermedia existente mas cercana). El productor ACTIVO confirmado de "
                "XXAFL_QAPV_TESTS_RL1 es exclusivamente la familia RL1Interface "
                "(RL1Interface, RL1Interface2, RL1InterfaceLocalMultiple, "
                "RL1PolInterfaceLocalMultiple), verificada con INSERT alcanzable y en produccion."
            ),
            extra={
                "code_present": True,
                "call_site_found": False,
                "files_checked_for_call_sites": GRABARL1_FILES_CHECKED,
                "active_confirmed_producer": [
                    "RL1Interface", "RL1Interface2/RL1Interface2",
                    "RL1InterfaceLocalMultiple", "RL1PolInterfaceLocalMultiple/RL1InterfaceLocalMultiple",
                ],
            },
        )
        return new_conf, new_label, new_op, evidence

    if eid in CONFLICT3_IDS:
        new_conf, new_label = old_row["confidence"], old_row["confidence_label"]  # sin cambio, ya es el maximo
        new_op = "insert"  # convencion ya usada en el modelo: insert tiene prioridad sobre update (ver edge id=64)
        relations = evidence.get("relations", [])
        if relations and isinstance(relations, list):
            write_ops = relations[0].setdefault("write_ops", [])
            already_has_insert = any(w.get("operation") == "insert" for w in write_ops)
            if not already_has_insert:
                write_ops.insert(0, SMARTCHECK_INSERT_WRITE_OP)
        evidence["reconciliation"] = build_reconciliation_block(
            "CONFLICT_3_INTERCONFIG_SMARTCHECK_INSERT_UPDATE", old_row, new_conf, new_label, new_op,
            reason=(
                "InterConfig.ProcSmartCheck (InterAFL.ViewModel/ProcVM.cs) ejecuta DOS operaciones "
                "sobre XXAFL_QAPV_TESTS_SMART_CHECK dentro del MISMO flujo: (1) linea 8803, INSERT "
                "literal completo con los datos de inspeccion de 24 fibras leidos de un archivo .xlsx "
                "del instrumento MPO -- la creacion real del resultado; (2) linea 8897, UPDATE "
                "posterior que solo adjunta FILE_IMAGE tras localizar el archivo de imagen -- "
                "enriquecimiento/backfill, ya detectado correctamente por el Analyzer. El INSERT no "
                "estaba reflejado. Se agrega el write_op de insert al array evidence.relations[0]."
                "write_ops (patron ya usado en 2062 edges existentes de este mismo dataset, ej. id=64) "
                "sin eliminar el write_op de update existente, y se actualiza el campo operation "
                "top-level de 'update' a 'insert' siguiendo la misma convencion ya vigente (insert "
                "tiene prioridad quando coexisten ambas operaciones). confidence/label no cambian: "
                "ya estaban en el techo (CONFIRMED/85) y la evidencia nueva los refuerza, no los debilita."
            ),
        )
        return new_conf, new_label, new_op, evidence

    return old_row["confidence"], old_row["confidence_label"], old_row["operation"], evidence


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id FROM lineage_runs WHERE id = 3")
    if cur.fetchone():
        raise SystemExit("ABORT: lineage_run id=3 ya existe. Script es de un solo uso, no se re-ejecuta.")

    cur.execute("SELECT * FROM lineage_runs WHERE id = ?", (SOURCE_RUN_ID,))
    source_run = cur.fetchone()
    if not source_run:
        raise SystemExit(f"ABORT: no existe lineage_run id={SOURCE_RUN_ID}")

    cur.execute("SELECT * FROM lineage_edges WHERE lineage_run_id = ? ORDER BY id", (SOURCE_RUN_ID,))
    source_edges = cur.fetchall()

    all_conflict_ids = CONFLICT1_IDS | CONFLICT2_IDS | CONFLICT3_IDS
    found_ids = {r["id"] for r in source_edges if r["id"] in all_conflict_ids}
    missing = all_conflict_ids - found_ids
    if missing:
        raise SystemExit(f"ABORT: {len(missing)} edge ids esperados no se encontraron en run {SOURCE_RUN_ID}: {missing}")

    print(f"Run origen: {SOURCE_RUN_ID}, edges totales: {len(source_edges)}")
    print(f"Conflicto 1 (CopyVIAVI->SMART_CHECK):  {len(CONFLICT1_IDS)} edges")
    print(f"Conflicto 2 (GrabaRL1 dead code):       {len(CONFLICT2_IDS)} edges")
    print(f"Conflicto 3 (InterConfig SMART_CHECK):  {len(CONFLICT3_IDS)} edges")
    print(f"Total a reconciliar: {len(all_conflict_ids)} / {len(source_edges)}")
    print(f"Sin cambio (copia identica): {len(source_edges) - len(all_conflict_ids)}")

    try:
        cur.execute(
            """INSERT INTO lineage_runs
               (started_at, completed_at, status, app_count, edge_count, triggered_by, error_message, analyzer_version)
               VALUES (?, ?, 'completed', ?, ?, ?, NULL, ?)""",
            (NOW, NOW, source_run["app_count"], len(source_edges),
             "forensic_reconciliation_conflicts_1_2_3", source_run["analyzer_version"]),
        )
        new_run_id = cur.lastrowid
        assert new_run_id == 3, f"lineage_run inesperado id={new_run_id}, se esperaba 3"

        cols = [
            "lineage_run_id", "from_app_id", "from_app_name", "to_app_id", "to_app_name",
            "object_name", "object_kind", "operation", "server_kind", "server_name",
            "database_name", "is_oracle_erp_table", "confidence", "confidence_label",
            "detection_method", "evidence_json", "observed_at",
        ]
        insert_sql = f"INSERT INTO lineage_edges ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"

        n_unchanged = 0
        n_changed = 0
        for row in source_edges:
            new_conf, new_label, new_op, evidence = reconcile_edge(row)
            changed = row["id"] in all_conflict_ids
            observed_at = NOW if changed else row["observed_at"]
            values = (
                new_run_id, row["from_app_id"], row["from_app_name"], row["to_app_id"], row["to_app_name"],
                row["object_name"], row["object_kind"], new_op, row["server_kind"], row["server_name"],
                row["database_name"], row["is_oracle_erp_table"], new_conf, new_label,
                row["detection_method"], json.dumps(evidence), observed_at,
            )
            cur.execute(insert_sql, values)
            if changed:
                n_changed += 1
            else:
                n_unchanged += 1

        conn.commit()
        print(f"\nOK. lineage_run id={new_run_id} creada.")
        print(f"Edges copiados sin cambio: {n_unchanged}")
        print(f"Edges reconciliados: {n_changed}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
