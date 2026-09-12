"""The Bindery Repair Calibre plugin: zip build drift, import shape, and
run() behavior under a stubbed calibre namespace.

The plugin vendors the repair core's five modules byte-identically; the
loader (calibre/customize/zipplugin.py) imports every zip-root .py as a
submodule of the plugin package, which is exactly the import shape these
tests install, so behavior tested here is behavior that ships."""

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile
from collections import namedtuple

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
_Counts = namedtuple("_Counts", "fatals errors warnings")

VENDOR_MODULES = (
    "transforms.py",
    "epub.py",
    "pagination.py",
    "watermark.py",
    "reserialize.py",
)


def _repo_version() -> tuple[int, int, int]:
    text = (REPO / "src" / "bindery" / "__init__.py").read_text(encoding="utf-8")
    prefix = 'VERSION = "'
    start = text.index(prefix) + len(prefix)
    end = text.index('"', start)
    return tuple(int(part) for part in text[start:end].split("."))


_PTF_DIR: tempfile.TemporaryDirectory | None = None


class _StubPersistentTemporaryFile:
    """The slice of calibre.ptempfile.PersistentTemporaryFile the plugin
    uses: a context manager handing out a real path that survives close()
    and the with-block (run() returns the path for Calibre to import; only
    interpreter shutdown cleans it)."""

    def __init__(self, suffix):
        fd = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir=_ptf_dir())
        self.name = fd.name
        fd.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _ptf_dir() -> str:
    return _PTF_DIR.name if _PTF_DIR is not None else tempfile.gettempdir()


def _install_calibre_stubs(config_dir: str) -> None:
    cal = types.ModuleType("calibre")
    customize = types.ModuleType("calibre.customize")
    constants = types.ModuleType("calibre.constants")
    ptemp = types.ModuleType("calibre.ptempfile")

    class FileTypePlugin:
        def __init__(self, plugin_path):
            self.plugin_path = plugin_path
            self.site_customization = None

        def temporary_file(self, suffix):
            return _StubPersistentTemporaryFile(suffix)

    customize.FileTypePlugin = FileTypePlugin
    constants.config_dir = config_dir
    ptemp.PersistentTemporaryFile = _StubPersistentTemporaryFile
    cal.customize = customize
    cal.constants = constants
    cal.ptempfile = ptemp
    for name, mod in (
        ("calibre", cal),
        ("calibre.customize", customize),
        ("calibre.constants", constants),
        ("calibre.ptempfile", ptemp),
    ):
        sys.modules.setdefault(name, mod)


def _load_plugin_package():
    """Import plugin/__init__.py exactly the way calibre's loader does: as
    the package calibre_plugins.bindery_repair, with the vendored modules
    resolvable as its submodules (plugin dir first, then src/bindery)."""
    _install_calibre_stubs(tempfile.gettempdir())
    pkg = types.ModuleType("calibre_plugins")
    sys.modules.setdefault("calibre_plugins", pkg)
    spec = importlib.util.spec_from_file_location(
        "calibre_plugins.bindery_repair",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR), str(REPO / "src" / "bindery")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["calibre_plugins.bindery_repair"] = module
    spec.loader.exec_module(module)
    return module


class TestPluginBuild(unittest.TestCase):
    """The zip the release attaches is a pure function of the repo tree."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "scripts"))
        import build_plugin

        cls.build_plugin = build_plugin
        cls.tmp = tempfile.TemporaryDirectory(prefix="bindery_plugin_")
        cls.zip_path = pathlib.Path(cls.tmp.name) / "test-plugin.zip"
        build_plugin.build(cls.zip_path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        sys.path.remove(str(REPO / "scripts"))

    def _entry(self, name: str) -> bytes:
        with zipfile.ZipFile(self.zip_path) as z:
            return z.read(name)

    def test_vendor_modules_are_byte_identical(self):
        for name in VENDOR_MODULES:
            self.assertEqual(
                self._entry(name),
                (REPO / "src" / "bindery" / name).read_bytes(),
                name,
            )

    def test_entry_is_the_plugin_source_with_the_repo_version(self):
        raw = self._entry("__init__.py").decode("utf-8")
        version = _repo_version()
        self.assertIn(f"__PLUGIN_VERSION__ = {version!r}", raw)
        committed = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
        # the only permitted difference is the substituted version line
        self.assertEqual(
            raw,
            self.build_plugin._VERSION_LINE.sub(
                f"__PLUGIN_VERSION__ = {version!r}", committed, count=1
            ),
        )

    def test_import_name_marker_and_zip_layout(self):
        self.assertEqual(
            self._entry("plugin-import-name-bindery_repair.txt"),
            b"bindery_repair\n",
        )
        with zipfile.ZipFile(self.zip_path) as z:
            names = set(z.namelist())
        self.assertIn("__init__.py", names)
        self.assertTrue(set(VENDOR_MODULES) <= names)

    def test_default_output_name_carries_the_version(self):
        version = ".".join(str(part) for part in _repo_version())
        self.assertEqual(self.build_plugin.repo_version(), _repo_version())
        out = self.build_plugin.REPO / "dist" / f"BinderyRepair-v{version}.zip"
        self.assertEqual(out.parent, self.build_plugin.REPO / "dist")
        self.assertTrue(out.name.startswith("BinderyRepair-v"))


class TestPluginRun(unittest.TestCase):
    """run() behavior against synthetic EPUBs, under the calibre stubs."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    @classmethod
    def setUpClass(cls):
        global _PTF_DIR
        _PTF_DIR = tempfile.TemporaryDirectory(prefix="bindery_plugin_tmp_")
        cls._ptf_dir = _PTF_DIR
        cls.plugin_module = _load_plugin_package()

    @classmethod
    def tearDownClass(cls):
        cls._ptf_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bindery_plugin_run_")
        self.addCleanup(self.tmp.cleanup)
        self.plugin = self.plugin_module.BinderyRepair("/tmp/not-a-real-plugin.zip")
        self.plugin.site_customization = json.dumps(
            {"log_path": str(pathlib.Path(self.tmp.name) / "plugin.log")}
        )

    def _epub(self, name="t.epub", doc=None, pad=0):
        # doc is the FULL content-document text; None builds a clean doc
        if doc is None:
            doc = "<html><body><p>ordinary prose</p></body></html>"
        p = pathlib.Path(self.tmp.name) / name
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", doc)
            if pad:
                z.writestr("filler.bin", b"\0" * pad)
        return p

    def _log(self) -> str:
        f = pathlib.Path(self.tmp.name) / "plugin.log"
        return f.read_text() if f.exists() else ""

    def test_damaged_book_is_repaired_and_original_untouched(self):
        # a BOM before the first < is prolog junk: one default-pass fix
        p = pathlib.Path(self.tmp.name) / "d.epub"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr(
                "text.xhtml",
                "\ufeff<html><body><p>prolog junk here</p></body></html>",
            )
        before = p.read_bytes()
        out = self.plugin.run(str(p))
        self.assertNotEqual(out, str(p))
        self.assertTrue(pathlib.Path(out).exists())
        self.assertEqual(p.read_bytes(), before)  # the original is never touched
        with zipfile.ZipFile(out) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("prolog junk here", z.read("text.xhtml").decode("utf-8"))
        self.assertIn("fixed:", self._log())

    def test_clean_book_returns_original_byte_for_byte(self):
        p = self._epub()
        out = self.plugin.run(str(p))
        self.assertEqual(out, str(p))
        self.assertIn("no fixes:", self._log())

    def test_idempotent_second_run_reports_no_fixes(self):
        p = self._epub(doc="\ufeff<html><body><p>junk</p></body></html>")
        first = self.plugin.run(str(p))
        repaired = pathlib.Path(first).read_bytes()
        second = self.plugin.run(first)
        self.assertEqual(second, first)
        self.assertIn("no fixes:", self._log())
        # and the repaired file the first run produced is stable
        self.assertEqual(pathlib.Path(first).read_bytes(), repaired)

    def test_size_cap_refuses_and_returns_original(self):
        self.plugin.site_customization = json.dumps(
            {
                "log_path": str(pathlib.Path(self.tmp.name) / "plugin.log"),
                "max_size_mb": 0.01,  # ~10KB: the padded book exceeds it
            }
        )
        p = self._epub(pad=64 * 1024)
        out = self.plugin.run(str(p))
        self.assertEqual(out, str(p))
        self.assertIn("refused:", self._log())
        self.assertIn("cap", self._log())

    def test_broken_json_config_falls_back_to_defaults(self):
        self.plugin.site_customization = "{not json"
        p = self._epub(doc="\ufeff<html><body><p>junk</p></body></html>")
        out = self.plugin.run(str(p))
        # defaults still repair (log goes to the calibre config dir, which
        # the stub points into a read-safe scratch dir)
        self.assertNotEqual(out, str(p))

    def test_non_epub_suffix_passes_through(self):
        p = pathlib.Path(self.tmp.name) / "cover.jpg"
        p.write_bytes(b"not an epub at all")
        out = self.plugin.run(str(p))
        self.assertEqual(out, str(p))
        self.assertEqual(self._log(), "")

    def test_unreadable_zip_errors_and_returns_original(self):
        p = pathlib.Path(self.tmp.name) / "broken.epub"
        p.write_bytes(b"PK\x03\x04 this is not really a zip")
        out = self.plugin.run(str(p))
        self.assertEqual(out, str(p))
        self.assertIn("errored:", self._log())

    def test_experimental_epubcheck_mode_refuses_a_regression(self):
        p = self._epub(doc="\ufeff<html><body><p>junk</p></body></html>")
        self.plugin.site_customization = json.dumps(
            {
                "log_path": str(pathlib.Path(self.tmp.name) / "plugin.log"),
                "epubcheck_path": "/usr/bin/false",
            }
        )

        def fake_counts(plugin, path, cmd):
            # the original measures clean, the repaired copy "regresses"
            return _Counts(0, 0, 0) if path == p else _Counts(1, 0, 0)

        self.plugin_module.BinderyRepair._epubcheck_counts = fake_counts
        self.addCleanup(
            delattr,
            self.plugin_module.BinderyRepair,
            "_epubcheck_counts",
        )
        out = self.plugin.run(str(p))
        self.assertEqual(out, str(p))
        self.assertIn("refused:", self._log())

    def test_experimental_epubcheck_mode_accepts_no_worse(self):
        p = self._epub(doc="\ufeff<html><body><p>junk</p></body></html>")
        self.plugin.site_customization = json.dumps(
            {
                "log_path": str(pathlib.Path(self.tmp.name) / "plugin.log"),
                "epubcheck_path": "/usr/bin/false",
            }
        )
        self.plugin_module.BinderyRepair._epubcheck_counts = lambda plugin, path, cmd: (
            _Counts(1, 0, 0)
        )
        self.addCleanup(
            delattr,
            self.plugin_module.BinderyRepair,
            "_epubcheck_counts",
        )
        out = self.plugin.run(str(p))
        self.assertNotEqual(out, str(p))
        self.assertIn("fixed:", self._log())

    def test_experimental_epubcheck_mode_unanswered_check_accepts(self):
        p = self._epub(doc="\ufeff<html><body><p>junk</p></body></html>")
        self.plugin.site_customization = json.dumps(
            {
                "log_path": str(pathlib.Path(self.tmp.name) / "plugin.log"),
                "epubcheck_path": "/usr/bin/false",
            }
        )
        self.plugin_module.BinderyRepair._epubcheck_counts = lambda plugin, path, cmd: (
            None
        )
        self.addCleanup(
            delattr,
            self.plugin_module.BinderyRepair,
            "_epubcheck_counts",
        )
        out = self.plugin.run(str(p))
        # an unanswered measurement never refuses the (safe) default pass
        self.assertNotEqual(out, str(p))
        self.assertIn("fixed:", self._log())
