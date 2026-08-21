"""Application Flow -- Screen Surface Discovery, Incremento E (2026-08-21):
identifica, con evidencia estatica, los controles WinForms de cada pantalla
(Form/Window) ya descubierta por Application Structure Discovery (Incremento
A) -- nombre de control, tipo concreto, y texto visible cuando el codigo lo
demuestra explicitamente.

Cierra el cuello de botella identificado en la revision post-Incremento D:
la cadena Structure->Navigation->Interactions->DataFlow sabe que "Form1"
navega/llama/toca datos, pero no tenia ninguna evidencia de que controles
(botones, campos, etiquetas) componen esa pantalla.

Modulo SEPARADO de app_structure.py (decision confirmada durante la
investigacion previa: scan_app_files()/method_intervals ya exponen todo lo
necesario -- ClassInfo.line permite escopar declaraciones de campo por clase
sin necesitar limites de fin de clase, y method_intervals ya trae class_name
para escopar instanciacion/texto -- CERO cambios a app_structure.py).

PRECISION > COBERTURA: el tipo de un control NUNCA se infiere del nombre de
la variable (btnX no implica Button). Solo cuenta evidencia sintactica
directa: declaracion de campo con tipo del catalogo curado, y/o instanciacion
"this.X = new System.Windows.Forms.Y(...)" (el prefijo completo excluye
terceros -- ej. Microsoft.Reporting.WinForms.ReportViewer -- sin lista
negra). Identidad, tipo y texto son evidencias INDEPENDIENTES: la ausencia
de una nunca invalida las otras (ver ControlInfo).

Deliberadamente NO implementa en este incremento: superficie WPF (BAML sigue
siendo invisible al analisis estatico de .cs, mismo gap ya documentado en
A/B/C), layout/posicion/tamaño/estilo/atajos, integracion con Navigation/
Interactions/Data Flow, ni ninguna UI nueva."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from . import app_structure, confidence
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence
from .unknown import UnknownRecord

# Catalogo curado con evidencia real de las 5 apps de validacion (RefControl,
# TestValidation, Geometria/Release, AFL_DataCenter, EpoxyLabel) -- NUNCA se
# agrega un tipo solo porque "existe en WinForms". Explicito y ampliable:
# agregar un tipo nuevo requiere confirmarlo contra codigo real primero.
# Deliberadamente EXCLUIDOS: Timer/ToolTip/IContainer (componentes NO
# visuales, confirmados en el portafolio pero fuera del alcance de
# "superficie de pantalla" -- no tienen presencia visible que reconstruir).
CONTROL_CATALOG = frozenset({
    "Button", "TextBox", "Label", "ComboBox", "CheckBox", "RadioButton",
    "DataGridView", "DateTimePicker", "GroupBox", "PictureBox",
    "FlowLayoutPanel", "TableLayoutPanel",
})

# Declaracion de campo BARE (sin inicializador inline) -- "private TYPE
# name;" con cualquier modificador de acceso real observado en el portafolio
# (private/internal/public). El requisito de terminar en ";" inmediatamente
# despues del nombre excluye por construccion el caso real encontrado en
# Geometria/Release/DataTransfer.cs:753 ("private TextBox LeSerialCharola =
# new TextBox();" -- una variable-alias reasignada despues a controles
# reales, NUNCA un control genuino de la pantalla).
FIELD_DECL_RE = re.compile(
    r"^\s*(?:private|internal|protected|public)\s+(\w+)\s+(\w+)\s*;"
)

# Instanciacion real: "this.controlName = new System.Windows.Forms.Tipo(...)"
# -- el prefijo COMPLETO System.Windows.Forms excluye terceros (ej.
# Microsoft.Reporting.WinForms.ReportViewer, confirmado real en Geometria)
# sin necesitar una lista negra: simplemente no matchea.
INSTANTIATION_RE = re.compile(
    r"this\.(\w+)\s*=\s*new\s+System\.Windows\.Forms\.(\w+)\s*\("
)

# Texto explicito y literal: "this.controlName.Text = "algo";" -- NUNCA se
# infiere de resources.GetString(...)/concatenacion/variable (no observado
# en el portafolio; si apareciera, esta regex simplemente no matchea y
# label_text permanece None, nunca se inventa un texto).
TEXT_ASSIGNMENT_RE = re.compile(
    r'this\.(\w+)\.Text\s*=\s*"([^"]*)"\s*;'
)


@dataclass(frozen=True)
class ControlInfo:
    """Identidad (control_name), tipo (control_type) y texto (label_text)
    son evidencias INDEPENDIENTES -- la ausencia de una nunca destruye las
    otras. control_type=None + resolution_status="unresolved_control_type_
    unknown" (Caso B: tipo no identificado) es una situacion DISTINTA de
    control_type="Button" + label_text=None (Caso A: control conocido,
    texto no observado -- NUNCA se convierte en unknown)."""

    app_name: str
    class_name: str
    control_name: str
    control_type: str | None
    label_text: str | None
    resolution_status: str  # "resolved" | "unresolved_control_type_unknown"
    evidence: Evidence


@dataclass(frozen=True)
class ApplicationScreenSurface:
    app_name: str
    controls: tuple[ControlInfo, ...]
    # Heredados de ApplicationStructure (wiring WPF) MAS los propios de este
    # incremento (superficie de control WPF, ver discover_screen_surface) --
    # nunca se duplica el concepto de Unknown, cada limitacion distinta
    # tiene su propio reason_code, nunca colapsadas en una sola.
    unknowns: tuple[UnknownRecord, ...]


def _build_evidence(extractor_key: str, source_file: str, line_number: int, snippet: str) -> Evidence:
    return Evidence(
        source_file=source_file, line_number=line_number, snippet=snippet,
        extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _owning_class(class_starts: list[tuple[int, str]], line_idx: int) -> str | None:
    """Devuelve la clase cuya declaracion antecede inmediatamente a
    line_idx -- valido porque los rangos de clase en C# nunca se solapan:
    la clase con el mayor start_idx <= line_idx es, sin ambiguedad, la
    clase contenedora. No requiere conocer el FIN de ninguna clase (evita
    tocar app_structure.py para exponer limites que hoy no expone)."""
    owner = None
    for start_idx, class_name in class_starts:
        if start_idx <= line_idx:
            owner = class_name
        else:
            break
    return owner


def _scan_controls_in_file(app_name: str, rel_file: str, classes, method_intervals, lines) -> list[ControlInfo]:
    # Gap real descubierto validando contra el portafolio (2026-08-21):
    #
    # 1. TestValidation/TestValidation (WPF): el code-behind generado por el
    #    compilador XAML TAMBIEN declara campos bare de tipo catalogado para
    #    cada elemento con x:Name ("internal TextBox txtQtyPerBox;" ->
    #    System.Windows.Controls.TextBox, NO System.Windows.Forms.TextBox)
    #    -- la declaracion NUNCA trae el namespace, asi que "declaracion
    #    sola" es evidencia insuficiente para una clase WPF (class_type=
    #    "window").
    # 2. RefControl/Form1.cs: contiene una clase DTO anidada ("History",
    #    class_type="class", sin controles) declarada DENTRO del cuerpo de
    #    Form1. La heuristica "clase mas cercana hacia atras" asume que los
    #    rangos de clase nunca se solapan -- cierto entre clases HERMANAS,
    #    falso para clases ANIDADAS: los campos de control reales de Form1,
    #    declarados textualmente DESPUES de History, se atribuian
    #    incorrectamente a History.
    #
    # Ambos gaps se resuelven con el mismo fix: los candidatos de atribucion
    # se restringen a clases YA clasificadas "form" por Application
    # Structure Discovery -- una clase anidada no-form (History) o una
    # Window WPF nunca "eclipsa" a la clase form envolvente. Limitacion
    # residual aceptada y documentada: un campo de control declarado DENTRO
    # de una clase anidada no-form se atribuiria a la clase form envolvente
    # en vez de a la anidada -- no observado en el portafolio de validacion
    # (History no tiene campos, solo propiedades).
    class_starts = sorted((c.line - 1, c.class_name) for c in classes if c.class_type == "form")

    # 1. Declaraciones de campo BARE de tipo catalogado, escopadas por clase
    # form (ver nota arriba).
    declared: dict[tuple[str, str], tuple[str, int]] = {}
    for idx, line in enumerate(lines):
        m = FIELD_DECL_RE.match(line)
        if not m:
            continue
        type_name, ctrl_name = m.group(1), m.group(2)
        if type_name not in CONTROL_CATALOG:
            continue
        owning_class = _owning_class(class_starts, idx)
        if owning_class is None:
            continue
        declared.setdefault((owning_class, ctrl_name), (type_name, idx))

    # 2. Instanciaciones, escopadas por el intervalo de metodo (method_
    # intervals ya trae class_name -- no se necesita recalcular ambito).
    instantiated: dict[tuple[str, str], tuple[str, int, int, int]] = {}
    for start, end, class_name, _method_name in method_intervals:
        for idx in range(start, end + 1):
            m = INSTANTIATION_RE.search(lines[idx])
            if not m:
                continue
            ctrl_name, raw_type = m.group(1), m.group(2)
            key = (class_name, ctrl_name)
            instantiated.setdefault(key, (raw_type, idx, start, end))

    # 3. Texto explicito, buscado SOLO dentro del mismo intervalo de metodo
    # donde se encontro la instanciacion -- nunca en todo el archivo (evita
    # confundir el Text de diseno con reasignaciones de Text en logica de
    # negocio en otros metodos, ej. "txtRef.Text = dg[...].Value;").
    texts: dict[tuple[str, str], tuple[str, int]] = {}
    for key, (_raw_type, _inst_idx, m_start, m_end) in instantiated.items():
        _class_name, ctrl_name = key
        for idx in range(m_start, m_end + 1):
            m = TEXT_ASSIGNMENT_RE.search(lines[idx])
            if m and m.group(1) == ctrl_name:
                texts[key] = (m.group(2), idx)
                break

    controls: list[ControlInfo] = []
    for key in set(declared) | set(instantiated):
        class_name, ctrl_name = key
        inst = instantiated.get(key)
        decl = declared.get(key)

        if inst is not None and inst[0] in CONTROL_CATALOG:
            control_type, evidence_idx = inst[0], inst[1]
        elif decl is not None:
            control_type, evidence_idx = decl[0], decl[1]
        elif inst is not None:
            control_type, evidence_idx = None, inst[1]
        else:
            continue  # inalcanzable (key viene de declared|instantiated)

        label_text = texts.get(key, (None, None))[0]

        if control_type in CONTROL_CATALOG:
            resolution_status = "resolved"
            extractor_key = "APP_CONTROL_LABEL_TEXT" if label_text is not None else "APP_CONTROL_DECLARATION_AND_TYPE"
        else:
            resolution_status = "unresolved_control_type_unknown"
            extractor_key = "APP_CONTROL_TYPE_UNKNOWN"

        controls.append(ControlInfo(
            app_name=app_name,
            class_name=class_name, control_name=ctrl_name,
            control_type=control_type, label_text=label_text,
            resolution_status=resolution_status,
            evidence=_build_evidence(extractor_key, rel_file, evidence_idx + 1, lines[evidence_idx].strip()),
        ))

    return controls


def discover_screen_surface(app_id: int) -> ApplicationScreenSurface | None:
    """Punto de entrada del incremento -- deriva EN VIVO desde
    decompiled/<raiz>/, reutilizando integramente
    analyzer.app_structure.discover_application_structure()/scan_app_files()
    (intervalos de clase/metodo ya calculados, CERO cambios a
    app_structure.py). NUNCA usa resolve_data_flow_portfolio() ni recorre el
    portafolio completo -- el costo esta acotado a los archivos de ESTA app."""
    structure = app_structure.discover_application_structure(app_id)
    if structure is None:
        return None

    decompiled_root = app_structure.resolve_decompiled_root(structure.app_name)
    if not decompiled_root.is_dir():
        return ApplicationScreenSurface(app_name=structure.app_name, controls=(), unknowns=structure.unknowns)

    controls: list[ControlInfo] = []
    for rel_file, classes, _methods, _entry_points, method_intervals, lines in app_structure.scan_app_files(
        structure.app_name, decompiled_root
    ):
        controls.extend(_scan_controls_in_file(structure.app_name, rel_file, classes, method_intervals, lines))

    # Superficie WPF: NO implementada en este incremento (BAML sigue siendo
    # invisible al analisis estatico de .cs, mismo gap ya documentado 3
    # veces en A/B/C) -- se registra un Unknown explicito por cada Window
    # WPF confirmada, DISTINTO del ya existente sobre wiring de eventos
    # (son limitaciones semanticamente diferentes, nunca colapsadas).
    unknowns = list(structure.unknowns)
    for c in structure.classes:
        if c.class_type == "window":
            unknowns.append(UnknownRecord(
                app_name=structure.app_name, category="screen_surface",
                reason_code="wpf_control_surface_not_observable_in_cs",
                impact=(
                    f"La clase {c.class_name} es una Window WPF confirmada por evidencia C#, "
                    "pero su superficie de controles (TextBox/Button/Label equivalentes) vive "
                    "en XAML/BAML compilado, no observable por analisis estatico de .cs. No se "
                    "infiere ausencia de controles -- solo se declara la limitacion explicitamente."
                ),
                evidence_file=c.file, evidence_class=c.class_name,
                suggested_action="Revisar el XAML fuente manualmente si se necesita el inventario de controles de esta ventana.",
                priority="baja",
            ))

    return ApplicationScreenSurface(app_name=structure.app_name, controls=tuple(controls), unknowns=tuple(unknowns))
