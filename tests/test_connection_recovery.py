import importlib.util
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('bridge_recovery', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class ConnectionRecoveryTests(unittest.TestCase):
    def test_timeout_reaps_worker_but_does_not_claim_microphone_stopped(self):
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.side_effect = [True, False]
        connection = session.connection = MagicMock()
        connection.poll.return_value = False
        with self.assertRaisesRegex(RuntimeError, '超时.*检查豆包是否仍在录音'):
            session.command('stop')
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)
        self.assertTrue(session.uncertain)
        process.terminate.assert_called_once()

    def test_dead_worker_send_failure_is_reaped_and_later_probe_can_succeed(self):
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.return_value = False
        connection = session.connection = MagicMock()
        connection.send.side_effect = BrokenPipeError('dead worker')
        with self.assertRaisesRegex(RuntimeError, '连接中断'):
            session.command('cancel')
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)
        fresh_connection = MagicMock()
        fresh_connection.poll.return_value = True
        fresh_connection.recv.return_value = (True, True, 0)
        context = MagicMock()
        context.Pipe.return_value = fresh_connection, MagicMock()
        context.Process.return_value.is_alive.return_value = False
        with patch('multiprocessing.get_context', return_value=context):
            self.assertEqual(session.probe(), 0)
        self.assertFalse(session.uncertain)

    def test_session_probe_receives_state_and_reaps_read_only_child(self):
        session = m.VoiceSession('unused')
        connection = MagicMock()
        connection.poll.return_value = True
        connection.recv.return_value = (True, True, 0)
        child = MagicMock()
        process = MagicMock()
        process.is_alive.return_value = False
        context = MagicMock()
        context.Pipe.return_value = connection, child
        context.Process.return_value = process
        with patch('multiprocessing.get_context', return_value=context):
            self.assertEqual(session.probe(), 0)
        connection.send.assert_called_once_with('probe')
        child.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)

    def test_native_initialization_error_is_reported_and_window_is_destroyed(self):
        user = MagicMock()
        user.CreateWindowExW.return_value = 123
        connection = MagicMock()
        with patch.object(m.ctypes, 'WinDLL', return_value=user), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(m, 'NativeVoice', side_effect=RuntimeError('未验证的接口版本')):
            m.native_session(connection, 'unused')
        self.assertEqual(connection.send.call_args.args[0], (False, True, '未验证的接口版本'))
        user.DestroyWindow.assert_called_once_with(123)
        connection.close.assert_called_once()

    def test_probe_is_read_only_and_rejects_external_recording(self):
        client = object.__new__(m.NativeVoice)
        client.pipe = rb'\\.\pipe\ObricIme\oime-server'
        client.owned = False
        client.dll = MagicMock()
        client.call = MagicMock(return_value=0)
        kernel = MagicMock()
        kernel.WaitNamedPipeW.return_value = True
        with patch.object(m.ctypes, 'WinDLL', return_value=kernel):
            self.assertEqual(client.probe(), 0)
            client.call.assert_called_once_with(1006)
            client.dll.RpcPipe_FocusIn.assert_not_called()
            client.dll.RpcPipe_RegisterTsfNotifySink.assert_not_called()
            client.call.return_value = 3
            with self.assertRaisesRegex(RuntimeError, '其他|非本程序'):
                client.probe()
            client.owned = True
            self.assertEqual(client.probe(), 3)
            client.call.return_value = -1
            with self.assertRaisesRegex(RuntimeError, '状态'):
                client.probe()
            kernel.WaitNamedPipeW.return_value = False
            with self.assertRaisesRegex(RuntimeError, '管道'):
                client.probe()

    def test_probe_reports_live_session_state_without_fault(self):
        """录音/识别进行中是正常会话状态：只读探测必须如实返回，不得判成故障。"""
        client = object.__new__(m.NativeVoice)
        client.pipe = rb'\\.\pipe\ObricIme\oime-server'
        client.owned = True
        client.dll = MagicMock()
        kernel = MagicMock()
        kernel.WaitNamedPipeW.return_value = True
        with patch.object(m.ctypes, 'WinDLL', return_value=kernel):
            for state in (3, 6, 19, 22):
                client.call = MagicMock(return_value=state)
                self.assertEqual(client.probe(), state)
                self.assertEqual(client.state, state)
                client.dll.RpcPipe_FocusIn.assert_not_called()
            client.owned = False
            client.call = MagicMock(return_value=19)
            self.assertEqual(client.probe(), 19)
            client.call = MagicMock(return_value=3)
            with self.assertRaisesRegex(RuntimeError, '其他'):
                client.probe()

    def test_eof_releases_worker_and_allows_idle_recovery(self):
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.return_value = False
        connection = session.connection = MagicMock()
        connection.poll.return_value = True
        connection.recv.side_effect = EOFError('worker exited')
        with self.assertRaisesRegex(RuntimeError, '连接|退出'):
            session.command('start')
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)
        session.close()
        connection.close.assert_called_once()


    def test_none_connection_with_lingering_process_handles_stop_safely(self):
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.return_value = False
        session.connection = None
        self.assertFalse(session.command('stop'))
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)

    def test_release_clears_session_state_even_on_terminate_failure(self):
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.return_value = True
        session.connection = MagicMock()
        with self.assertRaisesRegex(RuntimeError, '未退出'):
            session._release(timeout=0)
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)

    def test_close_handles_none_connection_without_raising(self):
        session = m.VoiceSession('unused')
        session.process = MagicMock()
        session.process.is_alive.return_value = True
        session.connection = None
        session.close()
        self.assertIsNone(session.process)
        self.assertIsNone(session.connection)

    def test_v1_1_1_5_and_recent_rpc_hashes_allowed(self):
        self.assertIn('dd5fdf941e8aad8c09eddb09f5a3651be83aa1582c9e34bc13c845a77041797f', m.ALLOWED_RPC_SHA256)


if __name__ == '__main__':
    unittest.main(verbosity=2)
