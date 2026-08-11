"""Regresion de un bug real: discover_assemblies() usaba Path.rglob("*.exe")
y solo descartaba resultados de EXCLUDED_DIR_NAMES *despues* de recorrerlos
por completo -- sobre un share de red (\\naamrt-qcs25\\...), escanear la
carpeta raiz de un proyecto (en vez de directamente su bin\\Debug) hacia
GeoStatsInter parecia colgado indefinidamente. La causa real resulto doble:
1) obj\\, .vs\\, .git\\, packages\\ se recorrian por completo antes de
   descartarse (rglob no permite "no entrar", solo filtrar despues).
2) GeoStatsInter tambien tenia bin\\Debug\\logs y bin\\Debug\\logs - Copy
   (carpetas de log en tiempo de ejecucion acumuladas por la app durante su
   vida productiva) con tantos archivos que ni siquiera listarlas sin
   recursion terminaba en 180s por SMB.
Ver analyzer/decompile.py: discover_assemblies() ahora usa os.walk con poda
in-place de dirnames (nunca entra a esas carpetas) y _is_excluded_dir()
tambien excluye por prefijo cualquier carpeta que empiece con "logs"."""

from analyzer.decompile import discover_assemblies


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


class TestPrunesToolingAndVcsFolders:
    def test_finds_exe_in_bin_debug(self, tmp_path):
        _touch(tmp_path / "bin" / "Debug" / "App.exe")
        found = discover_assemblies(tmp_path)
        assert [p.name for p in found] == ["App.exe"]

    def test_skips_vshost_exe(self, tmp_path):
        _touch(tmp_path / "bin" / "Debug" / "App.exe")
        _touch(tmp_path / "bin" / "Debug" / "App.vshost.exe")
        found = discover_assemblies(tmp_path)
        assert [p.name for p in found] == ["App.exe"]

    def test_never_descends_into_obj_git_vs_packages(self, tmp_path):
        _touch(tmp_path / "bin" / "Debug" / "App.exe")
        _touch(tmp_path / "obj" / "Debug" / "Trap.exe")
        _touch(tmp_path / ".git" / "Trap.exe")
        _touch(tmp_path / ".vs" / "Trap.exe")
        _touch(tmp_path / "packages" / "Some.Package.1.0" / "Trap.exe")
        found = discover_assemblies(tmp_path)
        assert [p.name for p in found] == ["App.exe"]


class TestPrunesRuntimeLogFolders:
    """GeoStatsInter's real-world case: bin\\Debug\\logs and
    bin\\Debug\\logs - Copy, sitting right next to the real .exe."""

    def test_skips_logs_folder_next_to_exe(self, tmp_path):
        _touch(tmp_path / "bin" / "Debug" / "GeoStatsInter.exe")
        _touch(tmp_path / "bin" / "Debug" / "logs" / "2026-01-01.log")
        _touch(tmp_path / "bin" / "Debug" / "logs - Copy" / "2025-12-31.log")
        found = discover_assemblies(tmp_path)
        assert [p.name for p in found] == ["GeoStatsInter.exe"]

    def test_never_lists_inside_excluded_dirs(self, tmp_path, monkeypatch):
        """Prune must happen via dirnames[:] before os.walk descends -- not
        just filter matches afterward -- otherwise this regresses to the
        exact slow-scan bug being fixed here."""
        _touch(tmp_path / "bin" / "Debug" / "App.exe")
        logs_dir = tmp_path / "bin" / "Debug" / "logs"
        _touch(logs_dir / "a.log")

        import os as os_module

        visited = []
        real_walk = os_module.walk

        def spying_walk(top, *args, **kwargs):
            for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
                visited.append(dirpath)
                yield dirpath, dirnames, filenames

        monkeypatch.setattr("analyzer.decompile.os.walk", spying_walk)
        discover_assemblies(tmp_path)
        assert not any("logs" in v.lower() for v in visited)
