"""ADR-0004 -- tests de db.get_or_create_artifact()/save_analysis() con
artifact_evidence, y de apps.artifact_id. Corre siempre sobre una BD
temporal vacia (via db.init_db()), nunca sobre qapv_analyzer.db real --
mismo patron que tests/test_save_analysis_dedup.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import db  # noqa: E402
from analyzer.artifact import UNKNOWN, ArtifactEvidence  # noqa: E402
from analyzer.techstack import TechStack  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])


def _save(name, source_path, artifact_evidence=None):
    return db.save_analysis(name, source_path, _tech(), [], [], [], [], artifact_evidence=artifact_evidence)


class TestGetOrCreateArtifact:
    def test_real_binary_hash_creates_one_artifact(self, temp_db):
        app_id = _save("App1", r"\\srv\App1.exe", ArtifactEvidence(binary_hash="abc123"))

        row = db.get_app(app_id)["app"]
        assert row["artifact_id"] is not None
        with db.get_conn() as conn:
            artifact_row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (row["artifact_id"],)).fetchone()
        assert artifact_row["binary_hash"] == "abc123"

    def test_same_binary_hash_in_two_apps_reuses_the_same_artifact(self, temp_db):
        """Caso central de ADR-0004: mismo Artifact, dos Deployments (filas
        apps) distintas -- confirmado real con Polaridad/Release y
        DataTransfer v2.46/Release durante la investigacion."""
        id_a = _save("Polaridad/Release", r"C:\workstation_a\app.exe", ArtifactEvidence(binary_hash="0fa01484"))
        id_b = _save("IL-RL/Release", r"C:\workstation_b\app.exe", ArtifactEvidence(binary_hash="0fa01484"))

        artifact_a = db.get_app(id_a)["app"]["artifact_id"]
        artifact_b = db.get_app(id_b)["app"]["artifact_id"]
        assert artifact_a == artifact_b
        with db.get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM artifacts").fetchone()["c"]
        assert count == 1  # nunca se duplica el Artifact

    def test_different_binary_hashes_create_different_artifacts(self, temp_db):
        id_a = _save("AppA", r"\\srv\AppA.exe", ArtifactEvidence(binary_hash="hashA"))
        id_b = _save("AppB", r"\\srv\AppB.exe", ArtifactEvidence(binary_hash="hashB"))

        artifact_a = db.get_app(id_a)["app"]["artifact_id"]
        artifact_b = db.get_app(id_b)["app"]["artifact_id"]
        assert artifact_a != artifact_b

    def test_unknown_binary_hash_never_reused_as_identity(self, temp_db):
        """UNKNOWN significa 'se intento y no se pudo' -- dos analisis con
        binario inaccesible NUNCA deben fusionarse en el mismo Artifact
        solo por compartir el valor centinela."""
        id_a = _save("AppA", r"\\srv\AppA.exe", ArtifactEvidence(binary_hash=UNKNOWN))
        id_b = _save("AppB", r"\\srv\AppB.exe", ArtifactEvidence(binary_hash=UNKNOWN))

        artifact_a = db.get_app(id_a)["app"]["artifact_id"]
        artifact_b = db.get_app(id_b)["app"]["artifact_id"]
        assert artifact_a != artifact_b

    def test_source_hash_used_as_fallback_when_binary_hash_is_unknown(self, temp_db):
        id_a = _save("AppA", r"\\srv\AppA.exe", ArtifactEvidence(binary_hash=UNKNOWN, source_hash="src123"))
        id_b = _save("AppB", r"\\srv\AppB.exe", ArtifactEvidence(binary_hash=UNKNOWN, source_hash="src123"))

        artifact_a = db.get_app(id_a)["app"]["artifact_id"]
        artifact_b = db.get_app(id_b)["app"]["artifact_id"]
        assert artifact_a == artifact_b  # reutilizado por source_hash, evidencia secundaria fuerte

    def test_versions_are_preserved_but_never_used_as_identity(self, temp_db):
        app_id = _save("AppA", r"\\srv\AppA.exe", ArtifactEvidence(
            binary_hash="hashX", assembly_version="1.0.0.0",
            product_version="1.0.0.0", file_version="1.0.0.1",
        ))
        artifact_id = db.get_app(app_id)["app"]["artifact_id"]
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        assert row["assembly_version"] == "1.0.0.0"
        assert row["file_version"] == "1.0.0.1"
        # Reutilizacion sigue dependiendo SOLO de binary_hash/source_hash --
        # una version igual, con hash distinto, NUNCA fusiona.
        other_id = _save("AppB", r"\\srv\AppB.exe", ArtifactEvidence(
            binary_hash="hashY", assembly_version="1.0.0.0",
        ))
        assert db.get_app(app_id)["app"]["artifact_id"] != db.get_app(other_id)["app"]["artifact_id"]


class TestAppsArtifactIdBackwardCompatibility:
    def test_artifact_id_is_nullable_and_defaults_to_none(self, temp_db):
        """Llamador viejo (sin artifact_evidence) -- comportamiento
        historico preservado, nunca se inventa un Artifact sin evidencia."""
        app_id = _save("AppSinArtifact", r"\\srv\Legacy.exe")

        row = db.get_app(app_id)["app"]
        assert row["artifact_id"] is None

    def test_existing_save_analysis_signature_still_works_without_artifact_evidence(self, temp_db):
        """Ninguna llamada existente a save_analysis() debe romperse --
        misma firma posicional que antes, solo un parametro nuevo opcional
        al final."""
        app_id = db.save_analysis("AppVieja", r"\\srv\Vieja.exe", _tech(), [], [], [], [])
        assert app_id is not None
        assert db.get_app(app_id)["app"]["artifact_id"] is None


class TestReanalysisRecalculatesArtifact:
    def test_binary_changing_between_reanalysis_produces_a_different_artifact(self, temp_db):
        """ADR-0001: el binario puede cambiar aunque source_path/name sean
        iguales -- el artifact_id debe reflejar el hash MAS RECIENTE, nunca
        reutilizar ciegamente el de un analisis anterior."""
        source_path = r"\\srv\SameApp.exe"
        id_first = _save("SameApp", source_path, ArtifactEvidence(binary_hash="hash_v1"))
        artifact_first = db.get_app(id_first)["app"]["artifact_id"]

        id_second = _save("SameApp", source_path, ArtifactEvidence(binary_hash="hash_v2"))
        artifact_second = db.get_app(id_second)["app"]["artifact_id"]

        assert artifact_first != artifact_second

    def test_binary_staying_identical_between_reanalysis_reuses_the_same_artifact(self, temp_db):
        source_path = r"\\srv\SameApp.exe"
        id_first = _save("SameApp", source_path, ArtifactEvidence(binary_hash="hash_stable"))
        artifact_first = db.get_app(id_first)["app"]["artifact_id"]

        id_second = _save("SameApp", source_path, ArtifactEvidence(binary_hash="hash_stable"))
        artifact_second = db.get_app(id_second)["app"]["artifact_id"]

        assert artifact_first == artifact_second
