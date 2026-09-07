"""Genera un diagrama Mermaid (flowchart) que resume el flujo de datos de una
app ya analizada: que clases tocan que tablas/Stored Procedures, y que clases
usan que tipo de recurso externo (archivos, impresora, puerto serial, otro
proceso, red).

Se agrupa por CLASE (no por metodo/funcion) deliberadamente: apps con decenas
o cientos de hallazgos SQL producirian, a nivel de metodo, un grafo ilegible.
A nivel de clase el diagrama se mantiene legible y sigue respondiendo la
pregunta util: "que parte de la app toca que dato/recurso".

No requiere conexion a ninguna base de datos ni ejecuta nada — solo transforma
los hallazgos (SqlFinding/LocalIOFinding) ya extraidos por analyzer/extract.py."""

import re
from collections import defaultdict

from .extract import LocalIOFinding, SqlFinding

# Nodos totales (clases + tablas/SPs + recursos de IO) antes de truncar el
# diagrama — mas alla de esto Mermaid se vuelve lento e ilegible en el navegador.
MAX_NODES = 80

IO_CATEGORY_PATTERNS = [
    (("File.", "Directory.", "StreamReader", "StreamWriter", "FileStream", "DirectoryInfo"), "Archivos / carpetas"),
    (("PrintDocument", "PrintDialog", "PrinterSettings", "BarTender", "PrintOut"), "Impresora"),
    (("SerialPort",), "Puerto serial"),
    (("ModbusClient",), "PLC / Modbus"),
    (("Process.Start",), "Proceso externo"),
    (("HttpClient", "WebClient", "HttpWebRequest", "WebRequest", "SmtpClient"), "Red (HTTP/SMTP)"),
]


def _io_category(f: LocalIOFinding) -> str:
    # Fase 4 (KNOWN_LIMITATIONS.md L16/L17): un hallazgo de reflection/COM no
    # es "Otro I/O" -- es un riesgo de naturaleza distinta (comportamiento
    # depende de resolucion en tiempo de ejecucion), igual que en
    # report.py/export_office.py se le da su propia seccion/hoja separada de
    # la tabla de I/O comun. Se verifica category ANTES que los prefijos de
    # operation para no depender de que "Invoke"/"CreateInstance" nunca
    # coincida por accidente con un prefijo de I/O real.
    if f.category == "reflection":
        return "Reflection / COM"
    for prefixes, label in IO_CATEGORY_PATTERNS:
        if any(p in f.operation for p in prefixes):
            return label
    return "Otro I/O"


def _sanitize_id(text: str) -> str:
    """IDs de nodo en Mermaid no pueden tener espacios/simbolos raros."""
    return re.sub(r"[^\w]", "_", text)[:60] or "n"


def _escape_label(text: str) -> str:
    return text.replace('"', "'")


def build_dataflow_diagram(sql_findings: list[SqlFinding], io_findings: list[LocalIOFinding]) -> str | None:
    """Devuelve el texto fuente de un flowchart Mermaid, o None si la app no
    tiene ningun hallazgo SQL/IO (nada que dibujar)."""
    if not sql_findings and not io_findings:
        return None

    class_to_sql: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for f in sql_findings:
        if not f.target:
            continue
        kind = "table" if f.category == "query" else "sp"
        class_to_sql[f.class_name].add((kind, f.target))

    class_to_io: dict[str, set[str]] = defaultdict(set)
    for f in io_findings:
        class_to_io[f.class_name].add(_io_category(f))

    all_classes = sorted(set(class_to_sql) | set(class_to_io))
    if not all_classes:
        return None

    lines = ["flowchart LR"]
    resource_node_ids: dict[tuple[str, str], str] = {}
    node_count = 0
    truncated = False

    def resource_node(kind: str, label: str) -> str:
        nonlocal node_count
        key = (kind, label)
        if key in resource_node_ids:
            return resource_node_ids[key]
        node_id = f"{kind}_{_sanitize_id(label)}"
        resource_node_ids[key] = node_id
        safe_label = _escape_label(label)
        if kind == "table":
            lines.append(f'    {node_id}[("{safe_label}")]')
        elif kind == "sp":
            lines.append(f'    {node_id}[["{safe_label}"]]')
        else:
            lines.append(f'    {node_id}("{safe_label}")')
        node_count += 1
        return node_id

    for class_name in all_classes:
        if node_count >= MAX_NODES:
            truncated = True
            break
        class_id = f"class_{_sanitize_id(class_name)}"
        lines.append(f'    {class_id}["{_escape_label(class_name)}"]:::classNode')
        node_count += 1

        for kind, target in sorted(class_to_sql.get(class_name, [])):
            if node_count >= MAX_NODES:
                truncated = True
                break
            node_id = resource_node(kind, target)
            lines.append(f"    {class_id} --> {node_id}")

        for io_label in sorted(class_to_io.get(class_name, [])):
            if node_count >= MAX_NODES:
                truncated = True
                break
            node_id = resource_node("io", io_label)
            lines.append(f"    {class_id} -.-> {node_id}")

    if truncated:
        lines.append(
            '    nota_truncado["... diagrama truncado (demasiados elementos) — ver la tabla completa abajo"]'
        )

    lines.append("    classDef classNode fill:#e1eef8,stroke:#1c6fc9,color:#17324d;")

    return "\n".join(lines)


def build_app_relations_diagram(app_name: str, produces_to, consumes_from, self_loops) -> str | None:
    """Mapa de Aplicaciones (2026-08-20): diagrama Mermaid ENFOCADO en una
    sola app -- ella al centro, flechas SOLO hacia relaciones FUERTES
    (productor->consumidor) ya derivadas por
    analyzer.data_flow.resolve_app_relations() (nunca relaciones debiles,
    que se presentan aparte como tabla, no como flecha). Reutiliza
    _sanitize_id/_escape_label/MAX_NODES -- mismo mecanismo y mismo limite
    que build_dataflow_diagram(), nunca una libreria nueva."""
    if not produces_to and not consumes_from and not self_loops:
        return None

    lines = ["flowchart LR"]
    node_ids: dict[str, str] = {}
    node_count = 0
    truncated = False

    def app_node(name: str, focal: bool = False) -> str:
        nonlocal node_count
        if name in node_ids:
            return node_ids[name]
        node_id = f"app_{_sanitize_id(name)}"
        node_ids[name] = node_id
        safe_label = _escape_label(name)
        if focal:
            lines.append(f'    {node_id}["{safe_label}"]:::focalNode')
        else:
            lines.append(f'    {node_id}("{safe_label}")')
        node_count += 1
        return node_id

    focal_id = app_node(app_name, focal=True)

    for rel in produces_to:
        if node_count >= MAX_NODES:
            truncated = True
            break
        other_id = app_node(rel.other_app)
        tables_label = ", ".join(sorted({ev.table for ev in rel.evidence}))
        lines.append(f'    {focal_id} -->|"{_escape_label(tables_label)}"| {other_id}')

    for rel in consumes_from:
        if node_count >= MAX_NODES:
            truncated = True
            break
        other_id = app_node(rel.other_app)
        tables_label = ", ".join(sorted({ev.table for ev in rel.evidence}))
        lines.append(f'    {other_id} -->|"{_escape_label(tables_label)}"| {focal_id}')

    # Self-loops no agregan nodos nuevos (ya es el nodo focal) -- se
    # muestran como una flecha del nodo a si mismo, sin contar contra
    # MAX_NODES (mismo criterio que build_dataflow_diagram, que solo limita
    # NODOS, nunca aristas).
    for loop in self_loops:
        lines.append(f'    {focal_id} -->|"{_escape_label(loop.table)}"| {focal_id}')

    if truncated:
        lines.append(
            '    nota_truncado["... relaciones adicionales truncadas — ver las tablas completas abajo"]'
        )

    lines.append("    classDef focalNode fill:#e1eef8,stroke:#1c6fc9,color:#17324d,font-weight:bold;")

    return "\n".join(lines)


def build_lineage_diagram(tree, root_kind: str = "app") -> str | None:
    """Data Lineage Explorer (2026-09): flowchart TD (top-down se lee mejor
    que LR para una cadena de varios niveles) de un LineageTree ya acotado
    (analyzer.data_lineage.resolve_lineage_for_app/_for_object). Reutiliza
    MAX_NODES/_sanitize_id/_escape_label -- mismo mecanismo, mismo limite que
    build_app_relations_diagram, deliberadamente NINGUNA libreria JS nueva
    (ver ARCHITECTURAL_PRINCIPLES.md Principio 4: sin evidencia de que
    Mermaid no alcance para esto).

    Nodos = SOLO apps (igual decision que build_app_relations_diagram): el
    nombre del objeto de datos va como ETIQUETA de la arista, nunca como su
    propio nodo -- en un arbol multi-nivel, un nodo por objeto agotaria
    MAX_NODES mucho antes que en el Mapa de Aplicaciones de un solo salto.
    Los nodos DIRECT_ORACLE (root, o cualquier hop cuyo server_kind=='oracle')
    reciben su propio classDef para anclar visualmente donde nace la cadena."""
    if tree is None or not tree.paths:
        return None

    lines = ["flowchart TD"]
    node_ids: dict[str, str] = {}
    oracle_nodes: set[str] = set()
    node_count = 0
    truncated = tree.truncated
    edges_drawn: set[tuple[str, str, str]] = set()

    def app_node(name: str) -> str:
        nonlocal node_count
        if name in node_ids:
            return node_ids[name]
        node_id = f"app_{_sanitize_id(name)}"
        node_ids[name] = node_id
        node_count += 1
        return node_id

    for path in tree.paths:
        for hop in path.hops:
            if node_count >= MAX_NODES:
                truncated = True
                break
            from_id = app_node(hop.from_app_name)
            to_id = app_node(hop.to_app_name)
            if hop.server_kind == "oracle":
                oracle_nodes.add(hop.from_app_name)
            edge_key = (from_id, to_id, hop.object_name)
            if edge_key in edges_drawn:
                continue
            edges_drawn.add(edge_key)
            lines.append(f'    {from_id} -->|"{_escape_label(hop.object_name)}"| {to_id}')
        if node_count >= MAX_NODES:
            truncated = True
            break

    if root_kind == "app" and tree.root_name in node_ids:
        oracle_nodes.discard(tree.root_name)  # el classDef de root se aplica aparte, no se duplica

    node_decls = [f'    {node_id}["{_escape_label(name)}"]' for name, node_id in node_ids.items()]
    lines[1:1] = node_decls  # las declaraciones de nodo van antes de las aristas

    root_id = node_ids.get(tree.root_name)
    if root_id:
        lines.append(f"    class {root_id} rootNode;")
    if oracle_nodes:
        oracle_ids = ", ".join(node_ids[n] for n in oracle_nodes if n in node_ids)
        if oracle_ids:
            lines.append(f"    class {oracle_ids} oracleNode;")

    if truncated:
        lines.append('    nota_truncado["... linaje adicional truncado — ver la tabla de evidencia abajo"]')

    lines.append("    classDef rootNode fill:#e1eef8,stroke:#1c6fc9,color:#17324d,font-weight:bold;")
    lines.append("    classDef oracleNode fill:#f8dfc0,stroke:#b5651d,color:#5c3010;")

    return "\n".join(lines)
