"""静态约束常驻进程不直接创建原生客户端，不证明真实会话生命周期。"""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'


class LifecycleTests(unittest.TestCase):
    def test_resident_main_does_not_open_native_voice_client(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'NativeVoice']
        self.assertEqual(len(calls), 0, '常驻主进程不应直接创建原生语音客户端')


if __name__ == '__main__':
    unittest.main(verbosity=2)
