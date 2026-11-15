"""安装脚本静态合约；不代替真实安装与登录验收。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
    def test_per_user_install_owns_only_its_files_and_optional_startup(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        for required in ('PrivilegesRequired=lowest',
                         '#define AppMutex "Local\\DoubaoMouseVoiceBridge"',
                         'AppMutex={#AppMutex}',
                         '{userdesktop}', '{userstartup}', 'Parameters: "--background"',
                         'Flags: checkedonce', 'Flags: nowait postinstall skipifsilent unchecked'):
            self.assertIn(required, script)
        for forbidden in ('taskkill', 'HKLM', '[UninstallDelete]', '--stop'):
            self.assertNotIn(forbidden, script)

    def test_production_app_id_is_not_overridden_by_trial_builds(self):
        script = (ROOT / 'packaging/windows-installer.iss').read_text(encoding='utf-8-sig')
        self.assertIn('#define AppId "{{86667029-2D73-490C-A0FD-59830E33C283}"', script)
        self.assertIn('AppId={#AppId}', script)


if __name__ == '__main__':
    unittest.main()
