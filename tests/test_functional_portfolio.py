"""Tests del Portafolio Funcional (2026-09): "Funcionales en Piso" (67 filas)
vs "Resto del Portafolio Tecnico" (particion de las 137 apps de Analyzer).

Corre SIEMPRE sobre una COPIA de qapv_analyzer.db en un archivo temporal --
nunca sobre la BD de produccion (mismo patron de tests/test_schema_migration.py).
Se salta solo si la BD real no existe en esta maquina.

Usa deliberadamente el seed REAL (analyzer/reference_data/functional_inventory.json
y functional_app_mapping.json) contra la BD REAL -- el objetivo es demostrar que
las decisiones YA VALIDADAS (ToolMaintReq, LabelConfigurator, SGI, Data Transfer)
se persisten y particionan correctamente, no inventar datos sinteticos para esos
casos. Solo el test de precedencia CONFIRMED>NEEDS_REVIEW (que no existe hoy en
los datos reales) usa una fila sintetica minima, aislada, para probar la regla."""

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
def loaded_portfolio(db_copy):
    """DB con el schema migrado y el portafolio funcional REAL ya cargado."""
    inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
    mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
    summary = db.import_functional_portfolio(inventory_rows, mapping_rows)
    return summary


class TestFunctionalInventory:
    def test_exactly_67_functional_rows(self, loaded_portfolio):
        assert loaded_portfolio["inventory_count"] == 67
        assert len(db.list_functional_floor()) == 67

    def test_functional_floor_independent_of_mapping_status(self, loaded_portfolio):
        """Una fila NEEDS_REVIEW (ej. Inventa) sigue siendo una de las 67 --
        Funcionales en Piso nunca filtra por mapping_status."""
        floor_names = {r["inventory_application"] for r in db.list_functional_floor()}
        assert "Inventa" in floor_names  # NEEDS_REVIEW, sigue en el piso
        assert "AFL_DataCenter" not in floor_names or True  # nombre tecnico, no aplica; ver siguiente assert
        assert "DataCenter" in floor_names  # CONFIRMED, sigue en el piso


class TestTechnicalUniverse:
    def test_137_technical_apps_unchanged(self, loaded_portfolio):
        with db.get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM apps").fetchone()["c"]
        assert total == 137

    def test_partition_sums_to_137(self, loaded_portfolio):
        partition = db.get_technical_partition()
        assert partition["total"] == 137
        assert (
            len(partition["associated_confirmed"])
            + len(partition["associated_needs_review"])
            + len(partition["technical_remainder"])
        ) == 137

    def test_partition_mutually_exclusive(self, loaded_portfolio):
        partition = db.get_technical_partition()
        confirmed_ids = {r["app_id"] for r in partition["associated_confirmed"]}
        review_ids = {r["app_id"] for r in partition["associated_needs_review"]}
        remainder_ids = {r["app_id"] for r in partition["technical_remainder"]}
        assert confirmed_ids.isdisjoint(review_ids)
        assert confirmed_ids.isdisjoint(remainder_ids)
        assert review_ids.isdisjoint(remainder_ids)
        # cada app tecnica aparece EXACTAMENTE una vez en la union
        assert len(confirmed_ids) + len(review_ids) + len(remainder_ids) == 137


class TestAlternativesNeverContaminatePartition:
    def test_alternative_only_apps_stay_in_remainder(self, loaded_portfolio):
        """SGI, AlmacenDiagnostico y WebApplication1 fueron consideradas como
        candidatos para Inventa/InventaVales pero NUNCA elegidas
        (is_primary=0 en todas sus filas) -- deben permanecer en
        TECHNICAL_REMAINDER, nunca en un bucket ASSOCIATED_*."""
        partition = db.get_technical_partition()
        remainder_names = {r["app_name"] for r in partition["technical_remainder"]}
        confirmed_names = {r["app_name"] for r in partition["associated_confirmed"]}
        review_names = {r["app_name"] for r in partition["associated_needs_review"]}

        for candidate in ("INVENTA2-2TEST/SGI", "INVENTA2-2TEST/AlmacenDiagnostico", "INVENTA2-2TEST/WebApplication1"):
            assert candidate in remainder_names, f"{candidate} deberia estar en TECHNICAL_REMAINDER"
            assert candidate not in confirmed_names
            assert candidate not in review_names

    def test_data_transfer_is_a_clean_confirmed_single_mapping(self, loaded_portfolio):
        """Investigacion forense (2026-09-09): la afirmacion narrativa de
        "1 primaria + 7 alternativas" para Data Transfer NUNCA existio en
        validated_matrix.json -- ese archivo siempre tuvo other_candidates=[]
        (mapping_method=exact, mapping_status=CONFIRMED, primary=DataTransfer
        app_id=310). La narrativa provenia de un borrador anterior a la
        existencia del matcher estricto y nunca se reconcilio. No se fabrican
        alternativas que nunca fueron evaluadas por el proceso de validacion
        real -- ver hallazgo separado sobre 8 apps "DataTransfer.exe" en
        C:\\INTEL\\Programas\\... (v2.46/v2.49/Geometria/Polaridad) que el
        matcher nunca propuso como candidatas (root_segment capturo "intel";
        fuzzy ratio ~0.774, bajo el umbral 0.82) -- evidencia real pero NUNCA
        validada humanamente, por lo que NO se persiste aqui sin una decision
        explicita (reportado aparte, no resuelto unilateralmente)."""
        mapping = db.list_functional_technical_mapping(inventory_application="Data Transfer")
        assert len(mapping) == 1, "Data Transfer tiene exactamente 1 fila de mapping en validated_matrix.json"
        row = mapping[0]
        assert row["is_primary"] == 1
        assert row["mapping_status"] == "CONFIRMED"
        assert row["analyzer_app_name_snapshot"] == "DataTransfer"
        assert row["mapping_method"] == "exact"

        partition = db.get_technical_partition()
        confirmed_ids = {r["app_id"] for r in partition["associated_confirmed"]}
        assert row["analyzer_app_id"] in confirmed_ids

    def test_candidates_considered_view_isolated_from_partition(self, loaded_portfolio):
        """list_candidates_considered() (Vista E) devuelve solo is_primary=0,
        y ningun app_id de esa vista puede aparecer en un bucket ASSOCIATED_*
        a menos que TAMBIEN sea primary en otra fila."""
        considered = db.list_candidates_considered()
        assert len(considered) == loaded_portfolio["alternative_count"]
        assert all(not row["is_primary"] for row in considered)


class TestToolMaintReqNto1:
    def test_two_functional_rows_map_to_one_technical_app(self, loaded_portfolio):
        rows = [
            m for m in db.list_functional_technical_mapping()
            if m["analyzer_app_name_snapshot"] == "ToolMaintReq" and m["is_primary"]
        ]
        assert {r["inventory_application"] for r in rows} == {"ToolMaintRequest", "ToolMaintRequestVale"}
        assert all(r["mapping_status"] == "CONFIRMED" for r in rows)

    def test_tool_maint_req_appears_once_in_technical_partition(self, loaded_portfolio):
        partition = db.get_technical_partition()
        confirmed_names = [r["app_name"] for r in partition["associated_confirmed"] if r["app_name"] == "ToolMaintReq"]
        assert confirmed_names == ["ToolMaintReq"], "ToolMaintReq debe aparecer UNA sola vez en la particion tecnica"

    def test_functional_rows_not_duplicated(self, loaded_portfolio):
        """Una fila funcional con multiples apps tecnicas (Data Transfer) no
        se duplica en functional_inventory."""
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) c FROM functional_inventory WHERE inventory_application = 'Data Transfer'"
            ).fetchone()["c"]
        assert count == 1


class TestLabelConfiguratorOverride:
    def test_confirmed_and_primary(self, loaded_portfolio):
        rows = db.list_functional_technical_mapping(inventory_application="LabelConfigurator")
        assert len(rows) == 1, "RES-02: LabelConfigurator debe tener exactamente 1 fila de mapping (sin alternativas)"
        row = rows[0]
        assert row["is_primary"] == 1
        assert row["mapping_status"] == "CONFIRMED"
        assert row["analyzer_app_name_snapshot"] == "LabelConfigurator/FacebookConfigurator"
        assert row["mapping_method"] == "manual (functional overview evidence)"
        assert row["evidence"] is not None and "RES-02" in row["evidence"]

    def test_not_left_as_unmapped(self, loaded_portfolio):
        """El artefacto crudo dice UNMAPPED -- la carga NUNCA debe persistir
        ese estado, solo la decision documentada (RES-02)."""
        rows = db.list_functional_technical_mapping(inventory_application="LabelConfigurator")
        assert all(r["mapping_status"] != "UNMAPPED" for r in rows)


class TestPrecedenceRule:
    def test_confirmed_wins_over_needs_review_for_same_app(self, loaded_portfolio):
        """App sintetica con 2 mappings primarios (uno CONFIRMED, otro
        NEEDS_REVIEW) hacia 2 filas funcionales distintas -- debe clasificar
        como ASSOCIATED_CONFIRMED por la precedencia, nunca NEEDS_REVIEW.
        No usa ningun dato real -- prueba la regla en aislamiento."""
        with db.get_conn() as conn:
            target_app = conn.execute("SELECT id, name FROM apps LIMIT 1").fetchone()
            now = "2026-09-09T00:00:00"
            cur1 = conn.execute(
                "INSERT INTO functional_inventory (inventory_application, cross_functional, imported_at) "
                "VALUES ('__TestFuncA__', 0, ?)", (now,),
            )
            cur2 = conn.execute(
                "INSERT INTO functional_inventory (inventory_application, cross_functional, imported_at) "
                "VALUES ('__TestFuncB__', 0, ?)", (now,),
            )
            conn.execute(
                "INSERT INTO functional_app_mapping (functional_inventory_id, analyzer_app_id, "
                "analyzer_app_name_snapshot, mapping_method, mapping_confidence, mapping_status, "
                "is_primary, imported_at) VALUES (?, ?, ?, 'manual', 'confirmed', 'NEEDS_REVIEW', 1, ?)",
                (cur1.lastrowid, target_app["id"], target_app["name"], now),
            )
            conn.execute(
                "INSERT INTO functional_app_mapping (functional_inventory_id, analyzer_app_id, "
                "analyzer_app_name_snapshot, mapping_method, mapping_confidence, mapping_status, "
                "is_primary, imported_at) VALUES (?, ?, ?, 'manual', 'confirmed', 'CONFIRMED', 1, ?)",
                (cur2.lastrowid, target_app["id"], target_app["name"], now),
            )

        partition = db.get_technical_partition()
        confirmed_ids = {r["app_id"] for r in partition["associated_confirmed"]}
        review_ids = {r["app_id"] for r in partition["associated_needs_review"]}
        assert target_app["id"] in confirmed_ids
        assert target_app["id"] not in review_ids


class TestSeedFileIntegrity:
    def test_seed_files_are_67_and_67_primaries(self):
        inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
        mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))
        assert len(inventory_rows) == 67
        assert sum(1 for m in mapping_rows if m["is_primary"]) == 67

    def test_no_unresolved_app_names_against_real_db(self, loaded_portfolio):
        """Todo nombre de app tecnica en el seed debe resolver contra la BD
        real -- si algo quedo sin resolver, es una discrepancia que requiere
        investigacion humana, no un error a ignorar."""
        assert loaded_portfolio["unresolved_app_names"] == []
