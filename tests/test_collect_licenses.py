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
    def _probe(self, components):
        probe = ROOT / f'.tcl-probe-{id(self)}'
        for component in components:
            relative = dict(collect_licenses.TCL_TERM_COMPONENTS)[component]
            path = probe / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'{component} terms', encoding='utf-8')
        probe.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__('shutil').rmtree(probe, True))
        return probe

    def test_missing_tcl_terms_aborts_instead_of_warning(self):
        probe = self._probe(())
        with self.assertRaises(SystemExit):
            collect_licenses.require_tcl_terms(probe)

    def test_both_tcl_and_tk_terms_are_returned(self):
        probe = self._probe(('tcl', 'tk'))
        terms = collect_licenses.require_tcl_terms(probe)
        self.assertEqual(sorted(terms), ['tcl', 'tk'])

    def test_only_one_term_present_is_reported_as_missing(self):
        """只随附一份时不能静默：缺的那份必须能被报出来。"""
        probe = self._probe(('tk',))
        terms = collect_licenses.require_tcl_terms(probe)
        self.assertEqual(sorted(terms), ['tk'])
        missing = collect_licenses.missing_tcl_terms(terms)
        self.assertEqual(missing, ['tcl/tcl8.6/license.terms'])

    def test_both_terms_are_written_separately(self):
        """Tcl 与 Tk 正文必须各落一份，不能让其中一份顶替另一份。"""
        probe = self._probe(('tcl', 'tk'))
        terms = collect_licenses.require_tcl_terms(probe)

        target = ROOT / f'.tcl-out-{id(self)}'
        target.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(target, True))

        written = collect_licenses.write_tcl_terms(terms, target)
        self.assertEqual(written, ['tcl-license.terms', 'tk-license.terms'])
        self.assertEqual((target / 'tcl-license.terms').read_text(encoding='utf-8'),
                         'tcl terms')
        self.assertEqual((target / 'tk-license.terms').read_text(encoding='utf-8'),
                         'tk terms')

    def test_source_digest_mismatch_is_an_error(self):
        """摘掉 sha256 校验会让这个用例变红。"""
        item = {'filename': 'pynput-1.8.2.tar.gz',
                'digests': {'sha256': '0' * 64}}
        with self.assertRaises(RuntimeError):
            collect_licenses.check_source_digest(b'source bytes', item)

    def test_source_digest_match_returns_digest(self):
        import hashlib
        data = b'source bytes'
        item = {'filename': 'pynput-1.8.2.tar.gz',
                'digests': {'sha256': hashlib.sha256(data).hexdigest()}}
        self.assertEqual(collect_licenses.check_source_digest(data, item),
                         hashlib.sha256(data).hexdigest())

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
