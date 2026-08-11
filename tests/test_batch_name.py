"""Regresion: _batch_name() siempre devolvia "Raiz/Modulo", incluso cuando
Raiz y Modulo eran el mismo texto (una carpeta raiz con un solo proyecto,
p.ej. GeoStatsInter). Eso no solo era un nombre redundante -- causaba que
run_analysis() decompilara "GeoStatsInter/GeoStatsInter" DENTRO de la propia
carpeta de salida de la app standalone "GeoStatsInter", duplicando cada
hallazgo SQL/IO (ver test_save_analysis_dedup.py y pipeline.py). Ver
tambien group_apps_for_sidebar() en db.py, que ya colapsaba este mismo caso
para la barra lateral -- este fix aplica la misma regla en el origen."""

from app import _batch_name


class TestCollapsesRootEqualsModule:
    def test_single_project_root_collapses_to_bare_name(self):
        root = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\GeoStatsInter"
        exe = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\GeoStatsInter\bin\Debug\GeoStatsInter.exe"
        assert _batch_name(root, exe) == "GeoStatsInter"

    def test_multi_module_solution_keeps_root_slash_module(self):
        root = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\AFL.Dashboard"
        exe = r"\\naamrt-qcs25\Openshare\Fuentes MLN\QAPV_DATACENTER\AFL.Dashboard\AFL.Entrega\bin\Debug\AFL.Entrega.exe"
        assert _batch_name(root, exe) == "AFL.Dashboard/AFL.Entrega"
