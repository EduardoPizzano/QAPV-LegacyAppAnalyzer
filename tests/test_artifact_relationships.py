"""ADR-0004 -- tests de db.save_artifact_relationship(). PRECISION >
COBERTURA: una relacion tecnica (aunque sea "identical") NUNCA fusiona
ApplicationIdentity por si sola -- estos tests verifican que
human_resolution_state permanece separado y nunca se auto-asigna."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import confidence, db  # noqa: E402
from analyzer.artifact import ArtifactEvidence  # noqa: E402
from analyzer.techstack import TechStack  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=[])


def _artifact_id(name, source_path, binary_hash):
    app_id = db.save_analysis(
        name, source_path, _tech(), [], [], [], [],
        artifact_evidence=ArtifactEvidence(binary_hash=binary_hash),
    )
    return db.get_app(app_id)["app"]["artifact_id"]


class TestSaveArtifactRelationship:
    def test_relationship_persists_all_fields(self, temp_db):
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")
        b = _artifact_id("AppB", r"\\srv\AppB.exe", "hashB")

        rel_id = db.save_artifact_relationship(
            artifact_a_id=a, artifact_b_id=b, relationship_type="variant",
            confidence=confidence.resolve_confidence("ARTIFACT_RELATIONSHIP_MANUAL_EVIDENCE"),
            detection_method="manual_investigation", observed_at="2026-08-24T10:00:00",
            evidence="Geometria elimina el UPDATE de XXAFL_QAPV_RL1_PROCESS presente en v2.46",
            human_resolution_state="pending",
        )

        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM artifact_relationships WHERE id = ?", (rel_id,)).fetchone()
        assert row["relationship_type"] == "variant"
        assert "XXAFL_QAPV_RL1_PROCESS" in row["evidence"]
        assert row["confidence"] == 70
        assert row["detection_method"] == "manual_investigation"
        assert row["human_resolution_state"] == "pending"

    def test_identical_technical_relationship_does_not_touch_apps_or_application_identity(self, temp_db):
        """Una relacion 'identical' de alta confianza entre dos Artifacts
        (ej. dos builds que un analista determino equivalentes) NUNCA debe
        modificar apps.name/source_path de ninguna de las dos filas -- la
        relacion vive exclusivamente en artifact_relationships, nunca
        fusiona ApplicationIdentity por si sola (ADR-0002/0004)."""
        app_a_id = db.save_analysis(
            "Polaridad/Release", r"C:\ws_a\app.exe", _tech(), [], [], [], [],
            artifact_evidence=ArtifactEvidence(binary_hash="hash_a"),
        )
        app_b_id = db.save_analysis(
            "DataTransfer v2.46/Release", r"C:\ws_b\app.exe", _tech(), [], [], [], [],
            artifact_evidence=ArtifactEvidence(binary_hash="hash_b"),
        )
        a = db.get_app(app_a_id)["app"]["artifact_id"]
        b = db.get_app(app_b_id)["app"]["artifact_id"]
        before_a = db.get_app(app_a_id)["app"]["name"]
        before_b = db.get_app(app_b_id)["app"]["name"]

        db.save_artifact_relationship(
            artifact_a_id=a, artifact_b_id=b, relationship_type="identical",
            confidence=confidence.resolve_confidence("ARTIFACT_RELATIONSHIP_BINARY_HASH_MATCH"),
            detection_method="automated_hash", observed_at="2026-08-24T10:00:00",
        )

        assert db.get_app(app_a_id)["app"]["name"] == before_a
        assert db.get_app(app_b_id)["app"]["name"] == before_b

    def test_relationship_never_auto_assigns_confirmed_resolution_state(self, temp_db):
        """El sistema puede registrar la relacion tecnica, pero NUNCA debe
        auto-asignar human_resolution_state='confirmed_same_identity' --
        eso requiere confirmacion humana explicita (ADR-0002)."""
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")
        b = _artifact_id("AppB", r"\\srv\AppB.exe", "hashB")

        rel_id = db.save_artifact_relationship(
            artifact_a_id=a, artifact_b_id=b, relationship_type="similar",
            confidence=confidence.resolve_confidence("ARTIFACT_RELATIONSHIP_INSUFFICIENT_EVIDENCE"),
            detection_method="automated_diff", observed_at="2026-08-24T10:00:00",
        )
        with db.get_conn() as conn:
            row = conn.execute("SELECT human_resolution_state FROM artifact_relationships WHERE id = ?", (rel_id,)).fetchone()
        assert row["human_resolution_state"] is None  # nunca auto-confirmado

    def test_invalid_relationship_type_is_rejected(self, temp_db):
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")
        b = _artifact_id("AppB", r"\\srv\AppB.exe", "hashB")

        with pytest.raises(ValueError):
            db.save_artifact_relationship(
                artifact_a_id=a, artifact_b_id=b, relationship_type="totalmente_inventado",
                confidence=50, detection_method="manual_investigation", observed_at="2026-08-24T10:00:00",
            )

    def test_invalid_human_resolution_state_is_rejected(self, temp_db):
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")
        b = _artifact_id("AppB", r"\\srv\AppB.exe", "hashB")

        with pytest.raises(ValueError):
            db.save_artifact_relationship(
                artifact_a_id=a, artifact_b_id=b, relationship_type="fork",
                confidence=50, detection_method="manual_investigation", observed_at="2026-08-24T10:00:00",
                human_resolution_state="fusionado_automaticamente",
            )

    def test_a_b_and_b_a_never_produce_two_logical_duplicates(self, temp_db):
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")
        b = _artifact_id("AppB", r"\\srv\AppB.exe", "hashB")

        db.save_artifact_relationship(
            artifact_a_id=a, artifact_b_id=b, relationship_type="fork",
            confidence=50, detection_method="manual_investigation", observed_at="2026-08-24T10:00:00",
        )
        with pytest.raises(Exception):  # sqlite3.IntegrityError -- UNIQUE(artifact_a_id, artifact_b_id)
            db.save_artifact_relationship(
                artifact_a_id=b, artifact_b_id=a, relationship_type="fork",  # orden invertido
                confidence=50, detection_method="manual_investigation", observed_at="2026-08-24T10:00:01",
            )

    def test_artifact_cannot_relate_to_itself(self, temp_db):
        a = _artifact_id("AppA", r"\\srv\AppA.exe", "hashA")

        with pytest.raises(ValueError):
            db.save_artifact_relationship(
                artifact_a_id=a, artifact_b_id=a, relationship_type="identical",
                confidence=95, detection_method="automated_hash", observed_at="2026-08-24T10:00:00",
            )
