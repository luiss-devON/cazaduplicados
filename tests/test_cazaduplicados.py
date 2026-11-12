import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cazaduplicados import find_duplicates, quarantine, restore
from cazaduplicados.actions import MANIFEST_NAME, choose_keeper
from cazaduplicados.cli import main
from cazaduplicados.scanner import PARTIAL_BYTES, hash_file
from cazaduplicados.utils import format_size, parse_size


def write(path: Path, data: bytes, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class UtilsTest(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1023), "1023 B")
        self.assertEqual(format_size(1536), "1.5 KB")
        self.assertEqual(format_size(5 * 1024**3), "5.0 GB")

    def test_parse_size(self):
        self.assertEqual(parse_size("200"), 200)
        self.assertEqual(parse_size("10KB"), 10 * 1024)
        self.assertEqual(parse_size("1.5 mb"), int(1.5 * 1024**2))
        self.assertEqual(parse_size("2,5G"), int(2.5 * 1024**3))
        for bad in ("", "abc", "10XB", "-5"):
            with self.assertRaises(ValueError):
                parse_size(bad)


class ScannerTest(TempDirTest):
    def test_finds_identical_files_in_nested_folders(self):
        write(self.root / "a.txt", b"hola mundo")
        write(self.root / "sub" / "b.txt", b"hola mundo")
        write(self.root / "sub" / "deep" / "c.txt", b"hola mundo")
        write(self.root / "unico.txt", b"otro contenido")

        result = find_duplicates([self.root])

        self.assertEqual(result.files_scanned, 4)
        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual(len(group.paths), 3)
        self.assertEqual(group.wasted, 2 * len(b"hola mundo"))

    def test_same_size_different_content_is_not_duplicate(self):
        write(self.root / "a.bin", b"AAAA")
        write(self.root / "b.bin", b"BBBB")
        self.assertEqual(find_duplicates([self.root]).groups, [])

    def test_large_files_differing_only_at_the_end(self):
        base = os.urandom(PARTIAL_BYTES * 2)
        write(self.root / "a.bin", base + b"1")
        write(self.root / "b.bin", base + b"2")
        write(self.root / "c.bin", base + b"1")

        result = find_duplicates([self.root])

        self.assertEqual(len(result.groups), 1)
        names = sorted(p.name for p in result.groups[0].paths)
        self.assertEqual(names, ["a.bin", "c.bin"])

    def test_min_size_and_empty_files(self):
        write(self.root / "vacio1", b"")
        write(self.root / "vacio2", b"")
        write(self.root / "p1", b"xy")
        write(self.root / "p2", b"xy")
        self.assertEqual(len(find_duplicates([self.root]).groups), 1)  # vacíos ignorados
        self.assertEqual(find_duplicates([self.root], min_size=3).groups, [])

    def test_exclude_folder_names(self):
        write(self.root / "a.txt", b"dato")
        write(self.root / "node_modules" / "b.txt", b"dato")
        self.assertEqual(find_duplicates([self.root], exclude={"node_modules"}).groups, [])

    def test_overlapping_roots_do_not_create_false_duplicates(self):
        write(self.root / "sub" / "a.txt", b"solo uno")
        result = find_duplicates([self.root, self.root / "sub"])
        self.assertEqual(result.files_scanned, 1)
        self.assertEqual(result.groups, [])

    def test_hard_links_are_not_reported(self):
        original = write(self.root / "a.txt", b"enlazado")
        try:
            os.link(original, self.root / "b.txt")
        except (OSError, NotImplementedError):
            self.skipTest("el sistema de archivos no admite enlaces duros")
        self.assertEqual(find_duplicates([self.root]).groups, [])

    def test_groups_sorted_by_recoverable_space(self):
        write(self.root / "s1", b"x" * 10)
        write(self.root / "s2", b"x" * 10)
        write(self.root / "g1", b"y" * 1000)
        write(self.root / "g2", b"y" * 1000)
        groups = find_duplicates([self.root]).groups
        self.assertEqual([g.size for g in groups], [1000, 10])

    def test_hash_file_partial(self):
        path = write(self.root / "f", b"abcdef")
        self.assertEqual(hash_file(path, limit=3), hash_file(write(self.root / "g", b"abc")))


class ActionsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.data = self.root / "datos"
        self.old = write(self.data / "viejo.jpg", b"foto", mtime=1_000_000)
        self.new = write(self.data / "copias" / "nuevo.jpg", b"foto", mtime=2_000_000)
        self.cuarentena = self.root / "cuarentena"

    def test_choose_keeper_strategies(self):
        paths = [self.new, self.old]
        self.assertEqual(choose_keeper(paths, "antiguo"), self.old)
        self.assertEqual(choose_keeper(paths, "reciente"), self.new)
        self.assertEqual(choose_keeper(paths, "ruta-corta"), self.old)
        with self.assertRaises(ValueError):
            choose_keeper(paths, "inventada")

    def test_dry_run_moves_nothing(self):
        groups = find_duplicates([self.data]).groups
        moves = quarantine(groups, self.cuarentena, dry_run=True)
        self.assertEqual(len(moves), 1)
        self.assertTrue(self.new.exists())
        self.assertFalse(self.cuarentena.exists())

    def test_quarantine_and_restore_roundtrip(self):
        groups = find_duplicates([self.data]).groups
        moves = quarantine(groups, self.cuarentena, strategy="antiguo")

        self.assertEqual([m.original for m in moves], [self.new])
        self.assertTrue(self.old.exists())
        self.assertFalse(self.new.exists())
        self.assertTrue((self.cuarentena / MANIFEST_NAME).exists())

        restored, problems = restore(self.cuarentena)

        self.assertEqual(problems, [])
        self.assertEqual(restored, [self.new.absolute()])
        self.assertEqual(self.new.read_bytes(), b"foto")
        self.assertFalse((self.cuarentena / MANIFEST_NAME).exists())

    def test_restore_never_overwrites(self):
        quarantine(find_duplicates([self.data]).groups, self.cuarentena)
        write(self.new, b"archivo nuevo del usuario")

        restored, problems = restore(self.cuarentena)

        self.assertEqual(restored, [])
        self.assertEqual(len(problems), 1)
        self.assertEqual(self.new.read_bytes(), b"archivo nuevo del usuario")
        self.assertTrue((self.cuarentena / MANIFEST_NAME).exists())  # se conserva para reintentar

    def test_skips_file_changed_after_scan(self):
        groups = find_duplicates([self.data]).groups
        write(self.new, b"contenido distinto y mas largo")
        self.assertEqual(quarantine(groups, self.cuarentena), [])
        self.assertTrue(self.new.exists())


class CliTest(TempDirTest):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_scan_with_json_report(self):
        write(self.root / "d" / "a.txt", b"igual")
        write(self.root / "d" / "b.txt", b"igual")
        report = self.root / "informe.json"

        code, out, _ = self.run_cli("escanear", str(self.root / "d"), "-q", "--json", str(report))

        self.assertEqual(code, 0)
        self.assertIn("Grupos duplicados   : 1", out)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["archivos_sobrantes"], 1)
        self.assertEqual(data["recuperable_total"], 5)

    def test_missing_path_returns_error(self):
        code, _, err = self.run_cli("escanear", str(self.root / "no-existe"), "-q")
        self.assertEqual(code, 1)
        self.assertIn("no existe", err)

    def test_quarantine_inside_scanned_folder_is_excluded(self):
        write(self.root / "a.txt", b"repetido")
        write(self.root / "b.txt", b"repetido")
        cuarentena = self.root / "_cuarentena"

        code, out, _ = self.run_cli("cuarentena", str(self.root), "-q", "--destino", str(cuarentena))
        self.assertEqual(code, 0)
        self.assertIn("1 archivos", out)

        # un segundo escaneo no debe ver la cuarentena como duplicado
        code, out, _ = self.run_cli("escanear", str(self.root), "-q")
        self.assertIn("Grupos duplicados   : 0", out)

        code, out, _ = self.run_cli("restaurar", str(cuarentena))
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "a.txt").exists() and (self.root / "b.txt").exists())

    def test_invalid_size_argument(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            main(["escanear", str(self.root), "--min-tamano", "mucho"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
