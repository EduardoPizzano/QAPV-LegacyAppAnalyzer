"""Sidebar del Portafolio Funcional (2026-09): verifica que
db.group_apps_for_sidebar() refleje la particion tecnica YA VALIDADA
(vw_technical_app_classification) en dos secciones -- "Operativas en Piso"
(ASSOCIATED_CONFIRMED + ASSOCIATED_NEEDS_REVIEW) y "Resto del Portafolio"
(TECHNICAL_REMAINDER) -- sin recalcular ningun mapping y sin romper el
agrupamiento por raiz existente (grupos con miembros de clasificacion mixta,
ej. ServerMPO, deben partirse por aplicacion individual, nunca por grupo
completo).

Corre sobre una COPIA de qapv_analyzer.db con el seed REAL ya importado --
mismo patron que tests/test_functional_portfolio.py."""

import json
import shutil
from pathlib import Path

import pytest

from analyzer import db

REAL_DB_PATH = Path(__file__).parent.parent / "qapv_analyzer.db"
REFERENCE_DATA_DIR = Path(__file__).parent.parent / "analyzer" / "reference_data"

pytestmark = pytest.mark.skipif(
    not REAL_DB_PATH.is_file(),
    reason="qapv_analyzer.db no existe en esta maquina (no versionado, ver .gitignore)",
)


@pytest.fixture
def db_copy(tmp_path, monkeypatch):
    copy_path = tmp_path / "qapv_analyzer_test_copy.db"
    shutil.copy(REAL_DB_PATH, copy_path)
    monkeypatch.setattr(db, "DB_PATH", copy_path)
    db.init_db()
    return copy_path


@pytest.fixture
def sidebar(db_copy):
    inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
    mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
    db.import_functional_portfolio(inventory_rows, mapping_rows)
    return db.group_apps_for_sidebar()


def _floor_section(sidebar_items, section):
    matches = [it for it in sidebar_items if it["kind"] == "floor_section" and it["section"] == section]
    assert len(matches) == 1, f"debe existir exactamente 1 seccion '{section}'"
    return matches[0]


def _flatten_app_ids(group_or_single_items):
    """Aplana la lista de items (group/single) de una seccion a sus app ids."""
    ids = []
    for it in group_or_single_items:
        if it["kind"] == "group":
            ids.extend(m["id"] for m in it["members"])
        else:
            ids.append(it["app"]["id"])
    return ids


class TestSidebarPartition:
    """Los conteos de las secciones del sidebar NO son literalmente 62/75
    (=47+15 CONFIRMED+NEEDS_REVIEW / 75 TECHNICAL_REMAINDER de
    get_technical_partition()) porque esa particion es ciega a
    review_status. 23 apps reales tienen review_status='obsoleta' -- 2 de
    ellas SI tienen clasificacion funcional (Cortes/Cortes=CONFIRMED,
    AFL.ConsultaOperaciones=NEEDS_REVIEW) y 21 son TECHNICAL_REMAINDER. Como
    el requisito de esta tarea es que las obsoletas seguuan saliendo en su
    propia seccion tal como ya funcionaba (nunca duplicadas en piso/resto),
    el sidebar real reparte las 137 en 60 (piso) + 54 (resto) + 23
    (obsoletas) -- verificado, no forzado. Ver hallazgo reportado al usuario."""

    def test_piso_section_has_60_active_apps(self, sidebar):
        piso = _floor_section(sidebar, "piso")
        ids = _flatten_app_ids(piso["entries"])
        assert piso["count"] == 60
        assert len(ids) == 60

    def test_resto_section_has_54_active_apps(self, sidebar):
        resto = _floor_section(sidebar, "resto")
        ids = _flatten_app_ids(resto["entries"])
        assert resto["count"] == 54
        assert len(ids) == 54

    def test_piso_plus_resto_plus_obsolete_equals_137(self, sidebar):
        piso = _floor_section(sidebar, "piso")
        resto = _floor_section(sidebar, "resto")
        obsolete_items = [it for it in sidebar if it["kind"] == "obsolete_section"]
        obsolete_count = len(obsolete_items[0]["apps"]) if obsolete_items else 0
        with db.get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM apps").fetchone()["c"]
        assert piso["count"] + resto["count"] + obsolete_count == total == 137

    def test_technical_partition_still_reports_47_15_75_regardless_of_obsolete(self, sidebar):
        """La capa de datos (get_technical_partition, fuente de verdad) NO
        cambia por esta tarea -- sigue siendo 47/15/75 sin importar
        review_status. Solo la PRESENTACION en el sidebar resta las 2+21
        obsoletas de piso/resto para no romper la seccion de obsoletas."""
        partition = db.get_technical_partition()
        assert len(partition["associated_confirmed"]) == 47
        assert len(partition["associated_needs_review"]) == 15
        assert len(partition["technical_remainder"]) == 75

    def test_no_duplicates_between_sections(self, sidebar):
        piso_ids = set(_flatten_app_ids(_floor_section(sidebar, "piso")["entries"]))
        resto_ids = set(_flatten_app_ids(_floor_section(sidebar, "resto")["entries"]))
        assert piso_ids.isdisjoint(resto_ids)
        assert len(piso_ids) + len(resto_ids) == len(piso_ids | resto_ids)

    def test_no_remainder_app_in_piso(self, sidebar):
        partition = db.get_technical_partition()
        remainder_ids = {r["app_id"] for r in partition["technical_remainder"]}
        piso_ids = set(_flatten_app_ids(_floor_section(sidebar, "piso")["entries"]))
        assert piso_ids.isdisjoint(remainder_ids)

    def test_no_associated_app_in_resto(self, sidebar):
        partition = db.get_technical_partition()
        associated_ids = {r["app_id"] for r in partition["associated_confirmed"]} | {
            r["app_id"] for r in partition["associated_needs_review"]
        }
        resto_ids = set(_flatten_app_ids(_floor_section(sidebar, "resto")["entries"]))
        assert resto_ids.isdisjoint(associated_ids)

    def test_mixed_classification_group_splits_across_sections(self, sidebar):
        """ServerMPO tiene 2 miembros: Populator (is_primary=1, NEEDS_REVIEW)
        y AnnotatorV3 (nunca is_primary=1 en ningun lado -- TECHNICAL_REMAINDER).
        Deben terminar en secciones DISTINTAS, no en un mismo grupo."""
        piso_ids = set(_flatten_app_ids(_floor_section(sidebar, "piso")["entries"]))
        resto_ids = set(_flatten_app_ids(_floor_section(sidebar, "resto")["entries"]))
        with db.get_conn() as conn:
            populator_id = conn.execute("SELECT id FROM apps WHERE name = 'ServerMPO/Populator'").fetchone()["id"]
            annotator_id = conn.execute("SELECT id FROM apps WHERE name = 'ServerMPO/AnnotatorV3'").fetchone()["id"]
        assert populator_id in piso_ids
        assert annotator_id in resto_ids


class TestObsoleteUnaffected:
    def test_obsolete_section_still_present_and_excluded_from_floor_sections(self, sidebar):
        obsolete_items = [it for it in sidebar if it["kind"] == "obsolete_section"]
        if not obsolete_items:
            pytest.skip("No hay apps obsoletas en la BD real actual")
        obsolete_ids = {a["id"] for a in obsolete_items[0]["apps"]}
        piso_ids = set(_flatten_app_ids(_floor_section(sidebar, "piso")["entries"]))
        resto_ids = set(_flatten_app_ids(_floor_section(sidebar, "resto")["entries"]))
        assert obsolete_ids.isdisjoint(piso_ids)
        assert obsolete_ids.isdisjoint(resto_ids)


class TestSidebarWithoutFunctionalPortfolio:
    def test_no_piso_section_when_portfolio_not_imported(self, tmp_path, monkeypatch):
        """Sin functional_app_mapping poblada, TODAS las apps caen en
        TECHNICAL_REMAINDER -- no debe aparecer una seccion 'piso' vacia.

        Usa una BD sintetica vacia (init_db() puro), NUNCA una copia de
        qapv_analyzer.db real -- desde que Fase 7 (2026-09-09) importo el
        portafolio funcional a la BD real, esta SIEMPRE tiene
        functional_inventory poblada, asi que 'db_copy' ya no representa el
        escenario "portafolio no importado" que esta prueba necesita."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty_synthetic.db")
        db.init_db()
        items = db.group_apps_for_sidebar()
        piso_sections = [it for it in items if it["kind"] == "floor_section" and it["section"] == "piso"]
        assert piso_sections == []


class TestSidebarRouteRendersWithoutError:
    def test_index_route_renders_200_with_floor_sections(self, db_copy):
        import json as _json

        inventory_rows = _json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
        mapping_rows = _json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
        db.import_functional_portfolio(inventory_rows, mapping_rows)

        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        client = flask_app_module.app.test_client()
        response = client.get("/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "floor-section-piso" in body
        assert "floor-section-resto" in body


class TestSidebarI18n:
    """Fase 7.10 (2026-09-09): 'Operativas en Piso'/'Resto del Portafolio' se
    traducen via _(item.label_key) -- un valor DINAMICO tomado de un dict de
    Python, no un literal _('...') en el template. pybabel's static scanner
    nunca ve esos por si solo (ver analyzer/i18n_vocab.py) -- estos tests
    prueban la resolucion REAL de Flask-Babel via el mecanismo de sesion
    (/set_language/<lang>), no solo que el .po/.mo contengan las cadenas."""

    def _import_and_client(self, db_copy):
        import json as _json

        inventory_rows = _json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
        mapping_rows = _json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
        db.import_functional_portfolio(inventory_rows, mapping_rows)

        import app as flask_app_module
        flask_app_module.app.config["TESTING"] = True
        return flask_app_module.app.test_client()

    def test_spanish_locale_shows_spanish_labels(self, db_copy):
        client = self._import_and_client(db_copy)
        client.get("/set_language/es")
        body = client.get("/").get_data(as_text=True)
        assert "Operativas en Piso" in body
        assert "Resto del Portafolio" in body
        assert "Obsoletos" in body

    def test_english_locale_shows_english_labels(self, db_copy):
        client = self._import_and_client(db_copy)
        client.get("/set_language/en")
        body = client.get("/").get_data(as_text=True)
        assert "Operational Floor Apps" in body
        assert "Rest of Portfolio" in body
        assert "Obsolete" in body
        # nunca deben quedar los literales en espanol sin traducir en ingles
        assert "Operativas en Piso" not in body
        assert "Resto del Portafolio" not in body

    def test_english_catalog_has_no_fuzzy_entries_for_new_strings(self):
        from babel.messages.pofile import read_po

        po_path = Path(__file__).parent.parent / "translations" / "en" / "LC_MESSAGES" / "messages.po"
        with open(po_path, "rb") as f:
            catalog = read_po(f)
        for msgid in ("Operativas en Piso", "Resto del Portafolio"):
            message = catalog.get(msgid)
            assert message is not None, f"{msgid!r} no esta en el catalogo ingles"
            assert message.string, f"{msgid!r} tiene msgstr vacio"
            assert not message.fuzzy, f"{msgid!r} quedo marcado fuzzy"
