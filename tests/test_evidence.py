"""Tests de analyzer/evidence.py (Fase 1, Paso 1). Evidence es solo un
contenedor de datos en esta fase -- nadie lo llena con valores reales
todavia, asi que estos tests validan la FORMA (creacion default,
serializacion, valores parciales), no ningun comportamiento de extraccion."""

import dataclasses

from analyzer.__version__ import ANALYZER_VERSION
from analyzer.evidence import Evidence


class TestDefaultCreation:
    def test_evidence_with_no_args_does_not_raise(self):
        Evidence()

    def test_default_documents_not_instrumented_yet(self):
        """El default NO es un valor inventado -- documenta explicitamente
        que nada llamo a esto todavia (extractor='UNKNOWN', confidence bajo,
        sin linea/snippet)."""
        e = Evidence()
        assert e.extractor == "UNKNOWN"
        assert e.confidence == 20
        assert e.source_file is None
        assert e.line_number is None
        assert e.snippet is None
        assert e.pattern is None
        assert e.created_at is None

    def test_default_analyzer_version_matches_current(self):
        e = Evidence()
        assert e.analyzer_version == ANALYZER_VERSION


class TestSerialization:
    def test_asdict_contains_all_eight_fields(self):
        e = Evidence()
        d = dataclasses.asdict(e)
        expected_fields = {
            "source_file", "line_number", "snippet", "extractor",
            "pattern", "confidence", "analyzer_version", "created_at",
        }
        assert set(d.keys()) == expected_fields


class TestPartialValues:
    def test_constructing_with_some_kwargs_defaults_the_rest(self):
        e = Evidence(extractor="SETTINGS_DEFAULT_VALUE", confidence=95)
        assert e.extractor == "SETTINGS_DEFAULT_VALUE"
        assert e.confidence == 95
        # el resto sigue en su default, no se infiere nada
        assert e.source_file is None
        assert e.line_number is None

    def test_is_frozen_immutable(self):
        """Evidence es un value object -- una vez creado no deberia mutarse."""
        e = Evidence()
        with_error = False
        try:
            e.confidence = 99
        except dataclasses.FrozenInstanceError:
            with_error = True
        assert with_error
