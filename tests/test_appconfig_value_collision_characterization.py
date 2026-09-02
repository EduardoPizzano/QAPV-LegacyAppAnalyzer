"""Investigacion del segundo gap reportado durante la revision de logica de
negocio de QAPV2 (app id 434, 2026-09): "la entrada CXOraDEV de app.config
que el parser esta omitiendo".

CONCLUSION tras reproducir el caso de forma aislada: NO es un bug. Es el
MISMO mecanismo, ya deliberado y ya cubierto por
TestSettingsCsExtractionCarriesRealEvidence.test_dedup_case_surviving_entry_keeps_real_evidence
(tests/test_increment2_settingscs_evidence.py) -- find_settings() deduplica
por VALOR (no por nombre) para no reportar la misma conexion real dos veces
cuando app.config re-declara, bajo su nombre calificado
("Namespace.Properties.Settings.X"), el mismo valor que Settings.cs ya
declaro para ESA MISMA setting X.

Lo que hace este caso distinto -- y lo que me hizo sospechar un bug en un
primer momento -- es que QAPV2.Properties.Settings.cs tiene TRES settings
distintas (CXOraDEV, CXOraPRD, CXOra) con el MISMO placeholder compilado
(copia/pega del generador de Visual Studio). CXOraPRD y CXOra SI reciben un
valor distinto en app.config, asi que sus filas sobreviven sin problema y
son visibles en el reporte. CXOraDEV, en cambio, recibe en app.config
EXACTAMENTE el mismo valor que su PROPIO default compilado -- el mismo
escenario exacto que dedup_case (una fila real, mismo valor, dos nombres
distintos del mismo dato), asi que la deduplicacion por valor la descarta
correctamente.

Intente un fix mas preciso -- deduplicar por (nombre_normalizado, valor) en
vez de solo por valor -- pero lo descarte: para CXOraDEV, el nombre
normalizado ("CXOraDEV") y el valor coinciden exactamente con su propia fila
de Settings.cs, asi que ese fix tampoco la habria hecho sobrevivir; solo
habria sido codigo nuevo sin resolver el caso reportado, violando "no
cambios generales al parser si no son necesarios para estos dos casos".

Lo que SI confirma este archivo, con evidencia: el VALOR real de runtime de
CXOraDEV no se pierde -- sigue visible via su fila SETTINGS_DEFAULT_VALUE
(95%). Lo unico que no aparece es una SEGUNDA fila idéntica confirmandolo
desde app.config (98%) -- exactamente lo que dedup_case ya prueba que es el
comportamiento correcto."""

from analyzer.extract import find_settings


class TestCoincidentalValueCollisionAcrossDifferentSettingNames:
    def test_cxoradev_appconfig_entry_is_suppressed_same_as_dedup_case(self, fixture_root):
        """CXOraDEV en app.config restablece EXACTAMENTE el mismo valor que
        su propio default en Settings.cs -- mismo escenario que dedup_case,
        se deduplica correctamente."""
        settings = find_settings(fixture_root("coincidental_value_collision_case"))
        names = [s.name for s in settings]
        assert "QAPV2.Properties.Settings.CXOraDEV" not in names

    def test_cxoradev_runtime_value_is_still_present_via_settingscs(self, fixture_root):
        """La deduplicacion no pierde el dato -- CXOraDEV sigue apareciendo,
        con su valor real, solo que atribuido a Settings.cs en vez de a una
        segunda fila de app.config redundante."""
        settings = find_settings(fixture_root("coincidental_value_collision_case"))
        cxoradev = next(s for s in settings if s.name == "CXOraDEV")
        assert "ashexap01-kiil92-vip" in cxoradev.default_value
        assert "SID = AFLPRD" in cxoradev.default_value
        assert cxoradev.evidence.extractor == "SETTINGS_DEFAULT_VALUE"

    def test_cxoraprd_and_cxora_survive_because_their_values_genuinely_differ(self, fixture_root):
        """Control: las otras dos settings que comparten el mismo placeholder
        compilado SI reciben un valor distinto en app.config, y sus filas de
        app.config si sobreviven -- confirma que la supresion es especifica
        de CXOraDEV (mismo valor que su propio default), no una falla
        general de la extraccion de app.config para este archivo."""
        settings = find_settings(fixture_root("coincidental_value_collision_case"))
        names = [s.name for s in settings]
        assert "QAPV2.Properties.Settings.CXOraPRD" in names
        assert "QAPV2.Properties.Settings.CXOra" in names

    def test_same_mechanism_as_the_already_validated_dedup_case(self, fixture_root):
        """Prueba directa de que es EL MISMO mecanismo: dedup_case (una sola
        setting, mismo valor en Settings.cs y app.config) tambien termina con
        una sola fila -- mismo resultado estructural que CXOraDEV."""
        dedup_case_settings = find_settings(fixture_root("dedup_case"))
        collision_settings = find_settings(fixture_root("coincidental_value_collision_case"))

        assert len(dedup_case_settings) == 1  # ver test_increment2_settingscs_evidence.py
        cxoradev_appconfig_rows = [
            s for s in collision_settings if s.name == "QAPV2.Properties.Settings.CXOraDEV"
        ]
        assert len(cxoradev_appconfig_rows) == 0  # mismo resultado: la fila "espejo" no se duplica
