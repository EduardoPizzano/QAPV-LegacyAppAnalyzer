"""Regresion de un bug real: `DocumentFormat.OpenXml` (dependencia de ClosedXML,
ya bloqueado) no estaba en THIRD_PARTY_ASSEMBLY_PATTERN, asi que
find_companion_assemblies() la trataba como "posible codigo propio" y la
decompilaba entera -- 3528 de 3567 archivos .cs generados para EtiquetasRH
(98.9%) eran de esta sola libreria de terceros, sin ningun valor de negocio,
haciendo que el analisis pareciera colgado (>20 minutos). Ver analyzer/decompile.py."""

from analyzer.decompile import THIRD_PARTY_ASSEMBLY_PATTERN


class TestDocumentFormatOpenXmlIsBlocked:
    def test_documentformat_openxml_matches(self):
        assert THIRD_PARTY_ASSEMBLY_PATTERN.match("DocumentFormat.OpenXml")

    def test_documentformat_openxml_framework_variant_matches(self):
        """Version 3.0+ del paquete separa el ensamblado en dos DLLs."""
        assert THIRD_PARTY_ASSEMBLY_PATTERN.match("DocumentFormat.OpenXml.Framework")

    def test_closedxml_itself_still_matches(self):
        """No regresionar el caso que ya funcionaba -- ClosedXML depende de
        DocumentFormat.OpenXml, asi que las apps que usan una casi siempre
        traen la otra tambien."""
        assert THIRD_PARTY_ASSEMBLY_PATTERN.match("ClosedXML")


class TestInHouseAssembliesStillDecompile:
    """Confirma que el fix no se volvio tan amplio que empiece a bloquear
    ensamblados propios reales del portafolio."""

    def test_classlib_does_not_match(self):
        assert not THIRD_PARTY_ASSEMBLY_PATTERN.match("ClassLib")

    def test_autocompletetextbox_does_not_match(self):
        assert not THIRD_PARTY_ASSEMBLY_PATTERN.match("AutoCompleteTextBox")

    def test_connectcode_barcode_library_does_not_match(self):
        assert not THIRD_PARTY_ASSEMBLY_PATTERN.match("ConnectCodeBarcodeLibrary")
