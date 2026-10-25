"""验证一键自检不会把缺少进程或检查异常当作健康。"""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'doctor.py'
spec = importlib.util.spec_from_file_location('doctor_test_target', SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        doctor.RESULTS.clear()

    def tearDown(self):
        doctor.RESULTS.clear()

    def test_successful_empty_process_query_fails_health_check(self):
        with patch.object(doctor, 'run', return_value=(0, '', '')):
            doctor.check_process()
        self.assertEqual(doctor.RESULTS[0]['status'], 'fail')
        self.assertIn('未发现运行中', doctor.RESULTS[0]['detail'])

    def test_failed_process_query_is_not_a_pass(self):
        with patch.object(doctor, 'run', return_value=(1, '', 'query failed')):
            doctor.check_process()
        self.assertEqual(doctor.RESULTS[0]['status'], 'warn')

    def test_side_button_uses_probe_status_values(self):
        for status, expected in (('按键设置匹配', 'pass'), ('侧键未确认', 'warn'),
                                 ('侧键需检查', 'warn'), ('unknown', 'fail')):
            with self.subTest(status=status):
                doctor.RESULTS.clear()
                payload = doctor.json.dumps({'side_button_status': status, 'side_button': '状态'})
                with patch.object(doctor, 'run', return_value=(0, payload, '')):
                    doctor.check_side_button()
                self.assertEqual(doctor.RESULTS[0]['status'], expected)

    def test_default_skips_startup_shortcut(self):
        names = ('check_unittest', 'check_verify_tray', 'check_side_button',
                 'check_release_gate', 'check_process', 'check_log_health',
                 'check_doubao_install', 'check_startup_shortcut')
        with contextlib.ExitStack() as stack:
            checks = [stack.enter_context(patch.object(doctor, name)) for name in names]
            stack.enter_context(patch('sys.argv', ['doctor.py']))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(doctor.main(), 0)
        checks[-1].assert_not_called()
        for check in checks[:-1]:
            check.assert_called_once_with()
        self.assertIn({'check': 'startup_shortcut', 'status': 'skip',
                       'detail': '默认不检查登录启动项；可用 --check-startup 只读核对',
                       'evidence': ''}, doctor.RESULTS)

    def test_check_exception_reports_failure_and_continues(self):
        names = ('check_side_button', 'check_release_gate', 'check_process',
                 'check_log_health', 'check_doubao_install', 'check_startup_shortcut')
        with contextlib.ExitStack() as stack:
            checks = [stack.enter_context(patch.object(doctor, name)) for name in names]
            checks[0].side_effect = TimeoutError('检查超时')
            stack.enter_context(patch('sys.argv', ['doctor.py', '--quick', '--check-startup']))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = doctor.main()
        self.assertEqual(result, 1)
        self.assertIn('检查超时', output.getvalue())
        self.assertTrue(any(item['status'] == 'fail' for item in doctor.RESULTS))
        for check in checks:
            check.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
