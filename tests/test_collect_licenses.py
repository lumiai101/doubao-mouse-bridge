"""许可收集脚本的失败路径：缺项必须硬失败，不能静默出包。"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'collect_licenses_test_target', ROOT / 'scripts' / 'collect_licenses.py')
collect_licenses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect_licenses)


class CollectLicensesTests(unittest.TestCase):
    def test_missing_tcl_terms_aborts_instead_of_warning(self):
        probe = ROOT / f'.tcl-probe-{id(self)}'
        probe.mkdir()
        self.addCleanup(lambda: probe.rmdir() if probe.exists() else None)
        with self.assertRaises(SystemExit):
            collect_licenses.require_tcl_terms(probe)

    def test_present_tcl_terms_are_returned(self):
        probe = ROOT / f'.tcl-probe-{id(self)}'
        (probe / 'tcl' / 'tk8.6').mkdir(parents=True)
        (probe / 'tcl' / 'tk8.6' / 'license.terms').write_text('terms', encoding='utf-8')
        self.addCleanup(lambda: __import__('shutil').rmtree(probe, True))
        terms = collect_licenses.require_tcl_terms(probe)
        self.assertEqual(len(terms), 1)

    def test_pystray_has_no_sdist_so_wheel_is_the_source(self):
        urls = [
            {'packagetype': 'bdist_wheel', 'filename': 'pystray-0.19.5-py2.py3-none-any.whl'},
        ]
        item, kind = collect_licenses.pick_source(urls)
        self.assertEqual(kind, 'bdist_wheel')
        self.assertEqual(item['filename'], 'pystray-0.19.5-py2.py3-none-any.whl')

    def test_sdist_is_preferred_when_both_exist(self):
        urls = [
            {'packagetype': 'bdist_wheel', 'filename': 'pynput-1.8.2-py2.py3-none-any.whl'},
            {'packagetype': 'sdist', 'filename': 'pynput-1.8.2.tar.gz'},
        ]
        item, kind = collect_licenses.pick_source(urls)
        self.assertEqual(kind, 'sdist')
        self.assertEqual(item['filename'], 'pynput-1.8.2.tar.gz')

    def test_no_distributions_at_all_is_an_error(self):
        with self.assertRaises(RuntimeError):
            collect_licenses.pick_source([])


if __name__ == '__main__':
    unittest.main()
