"""安装脚本静态合约；不代替真实安装与登录验收。"""
import importlib.util
from pathlib import Path
import shutil
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'build_windows_test_target', ROOT / 'scripts' / 'build_windows.py')
build_windows = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_windows)


class InstallerContractTests(unittest.TestCase):
    def test_per_user_install_owns_only_its_files_and_optional_startup(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        for required in ('PrivilegesRequired=lowest',
                         'DefaultDirName={localappdata}\\Programs\\DoubaoMouseBridge',
                         '#define AppMutex "Local\\DoubaoMouseVoiceBridge"',
                         'AppMutex={#AppMutex}',
                         'CloseApplications=no', 'RestartApplications=no',
                         'DisableProgramGroupPage=yes', '{group}',
                         '{userdesktop}', '{userstartup}', 'Parameters: "--background"',
                         'Flags: checkedonce', 'Flags: nowait postinstall skipifsilent unchecked'):
            self.assertIn(required, script)
        for forbidden in ('taskkill', 'HKLM', '[UninstallDelete]', '--stop'):
            self.assertNotIn(forbidden, script)

    def test_desktop_shortcut_starts_with_settings_and_startup_shortcut_stays_in_tray(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        entries = [line.strip() for line in script.splitlines()
                   if line.strip().startswith('Name: "{userdesktop}')]
        self.assertEqual(len(entries), 1)
        self.assertNotIn('Parameters:', entries[0])
        startup = [line.strip() for line in script.splitlines()
                   if line.strip().startswith('Name: "{userstartup}')]
        self.assertEqual(len(startup), 1)
        self.assertIn('Parameters: "--background"', startup[0])

    def test_installer_version_has_no_built_in_default(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        self.assertIn('#error AppVersion is required', script)
        self.assertIn('AppVersion={#AppVersion}', script)
        self.assertNotIn('#define AppVersion "', script)

    def test_licenses_guard_runs_before_the_full_pyinstaller_build(self):
        output = ROOT / f'.build-guard-probe-{uuid.uuid4().hex}'
        self.addCleanup(shutil.rmtree, output, True)
        with self.assertRaises(ValueError):
            build_windows.build(output, compiler=Path('ISCC.exe'))
        self.assertFalse(output.exists())

    def test_production_app_id_is_not_overridden_by_trial_builds(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        self.assertIn('#define AppId "{{86667029-2D73-490C-A0FD-59830E33C283}"', script)
        self.assertIn('AppId={#AppId}', script)


if __name__ == '__main__':
    unittest.main()
