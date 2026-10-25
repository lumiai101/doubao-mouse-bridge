"""只读核对本机侧键与豆包设置；不监听、不录音、不打开软件、不写日志。"""
import importlib.util
import json
import os
from pathlib import Path
import sys

source = Path(__file__).resolve().parents[1] / 'src/doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('side_button_probe', source)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


def verify():
    buttons = bridge.read_logi_buttons()
    config = Path(os.environ['APPDATA']) / 'DoubaoIme/conf/config.json'
    bindings = bridge.read_bindings(config)
    status, detail = bridge.side_button_status(buttons, bindings)
    entries = {}
    for kind in ('doubao', 'ime', 'logi'):
        try:
            bridge.settings_target(kind)
            entries[kind] = '已找到入口；未启动软件或验证页面跳转'
        except (OSError, RuntimeError):
            entries[kind] = '未找到入口'
    print(json.dumps({'side_button_status': status, 'side_button': detail,
                      'doubao_shortcuts': bindings.label(), 'entries': entries,
                      'hardware_voice_test': '未执行'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify()
