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
        """仓库副本与解释器都没有 Tcl 正文时必须硬失败。"""
        probe = self._probe(())
        with self.assertRaises(SystemExit):
            collect_licenses.require_tcl_terms(probe, vendored=False)

    def test_vendored_tcl_terms_fill_the_gap(self):
        """仓库里的 Tcl 正文要顶上解释器缺的那一份，这是 N1 的核心。"""
        probe = self._probe(('tk',))
        terms = collect_licenses.require_tcl_terms(probe)
        self.assertEqual(sorted(terms), ['tcl', 'tk'])
        self.assertEqual(terms['tcl'], collect_licenses.VENDORED_TCL_TERMS)
        self.assertEqual(collect_licenses.missing_tcl_terms(terms), [])

    def test_vendored_terms_file_is_the_upstream_tcl_text(self):
        """vendor 进来的必须是 Tcl 8.6 的上游正文，不是 Tk 那份改名。"""
        import hashlib
        raw = collect_licenses.VENDORED_TCL_TERMS.read_bytes()
        self.assertEqual(len(raw), 2255)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            'c0a69a2bfd757361ec7e6143973b103c90409316b49e9c88db26ad6388e79f16')
        text = ' '.join(raw.decode('utf-8').split())
        self.assertIn('this notice is included verbatim in any distributions', text)
        self.assertIn('252.227-7014', text)

    def test_both_tcl_and_tk_terms_are_returned(self):
        probe = self._probe(('tcl', 'tk'))
        terms = collect_licenses.require_tcl_terms(probe, vendored=False)
        self.assertEqual(sorted(terms), ['tcl', 'tk'])

    def test_only_tk_present_aborts_because_tcl_is_mandatory(self):
        """Tcl 正文是必带项：仓库副本与解释器都没有时必须硬失败。"""
        probe = self._probe(('tk',))
        with self.assertRaises(SystemExit):
            collect_licenses.require_tcl_terms(probe, vendored=False)

    def test_both_terms_are_written_separately(self):
        """Tcl 与 Tk 正文必须各落一份，不能让其中一份顶替另一份。"""
        probe = self._probe(('tcl', 'tk'))
        terms = collect_licenses.require_tcl_terms(probe, vendored=False)

        target = ROOT / f'.tcl-out-{id(self)}'
        target.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(target, True))

        written = collect_licenses.write_tcl_terms(terms, target)
        self.assertEqual(written, ['tcl-license.terms', 'tk-license.terms'])
        self.assertEqual((target / 'tcl-license.terms').read_text(encoding='utf-8'),
                         'tcl terms')
        self.assertEqual((target / 'tk-license.terms').read_text(encoding='utf-8'),
                         'tk terms')

    def test_write_uses_vendored_tcl_when_interpreter_lacks_it(self):
        """本机只有 Tk 时，产物里仍要有 Tcl 正文（来自仓库）。"""
        probe = self._probe(('tk',))
        terms = collect_licenses.require_tcl_terms(probe)

        target = ROOT / f'.tcl-out-{id(self)}'
        target.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(target, True))

        written = collect_licenses.write_tcl_terms(terms, target)
        self.assertEqual(written, ['tcl-license.terms', 'tk-license.terms'])
        self.assertEqual(
            (target / 'tcl-license.terms').read_bytes(),
            collect_licenses.VENDORED_TCL_TERMS.read_bytes())

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


class LicenseObligationTests(unittest.TestCase):
    """N4：这些义务此前没有任何用例保护，改坏了不会变红。"""

    def test_collect_aborts_when_gpl_text_is_missing(self):
        """仓库里缺 GPLv3 正文时必须硬失败，不能悄悄少发一份。"""
        original = collect_licenses.LICENSE_SOURCE
        probe = ROOT / f'.gpl-probe-{id(self)}'
        probe.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(probe, True))

        try:
            collect_licenses.LICENSE_SOURCE = probe
            with self.assertRaises(SystemExit):
                collect_licenses.require_license_text('GPL-3.0.txt')
        finally:
            collect_licenses.LICENSE_SOURCE = original

    def test_collect_aborts_when_apache_text_is_missing(self):
        """仓库里缺 Apache-2.0 正文时同样必须硬失败。"""
        original = collect_licenses.LICENSE_SOURCE
        probe = ROOT / f'.apache-probe-{id(self)}'
        probe.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(probe, True))

        try:
            collect_licenses.LICENSE_SOURCE = probe
            with self.assertRaises(SystemExit):
                collect_licenses.require_license_text('Apache-2.0.txt')
        finally:
            collect_licenses.LICENSE_SOURCE = original

    def test_required_license_texts_are_shipped(self):
        """OpenSSL、GPLv3、Apache-2.0 三条声明对应的正文都必须在仓库里。"""
        for name in ('GPL-3.0.txt', 'Apache-2.0.txt', 'Tcl-8.6.txt'):
            path = collect_licenses.LICENSE_SOURCE / name
            self.assertTrue(path.is_file(), name)

    def test_third_party_notices_declares_openssl_and_tcl(self):
        """THIRD_PARTY_NOTICES.md 里 OpenSSL 与 Tcl/Tk 两条声明不能被删掉。"""
        text = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        self.assertIn('| OpenSSL |', text)
        self.assertIn('| Tcl/Tk |', text)
        self.assertIn('licenses/openssl/LICENSE.txt', text)
        self.assertIn('licenses/tcl-tk/NOTICE.txt', text)


if __name__ == '__main__':
    unittest.main()
