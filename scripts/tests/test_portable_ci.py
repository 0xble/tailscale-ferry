import importlib.util
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('ferry_ci', Path(__file__).resolve().parents[1] / 'ci.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def live(pid):
    if sys.platform == 'linux':
        try:
            status = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            return False
    else:
        status = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                                capture_output=True, text=True, timeout=2).stdout.strip()
    return bool(status) and not status.startswith(('Z', 'X'))


def descendant_cli(root, phase='stage', exit_code=None):
    """Copy the real entrypoint and driver, substituting only harmless tools."""
    for name in ('bin/ci', 'scripts/ci.py'):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ci.ROOT / name).read_bytes())
        target.chmod(0o755)
    tools = root / 'tools'
    tools.mkdir()
    ready, terminated = root / 'ready.json', root / 'term-received'
    script = f'''#!{sys.executable}
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path({str(root)!r})
ready = root / 'ready.json'
name = pathlib.Path(__file__).name
version = name == 'go' and sys.argv[1:] == ['version']
head = name == 'git' and 'rev-parse' in sys.argv
if '--descendant' in sys.argv:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: (root / 'term-received').touch())
    ready.write_text(json.dumps({{'leader':os.getppid(), 'child':os.getpid(),
                                'group':os.getpgrp(), 'home':os.environ['HOME']}}))
    time.sleep(30)
else:
    phase = {phase!r}
    selected = (phase in ('stage', 'launch') and name == 'go' and not version) or (phase == 'version' and version) or (phase == 'git' and head)
    if phase == 'final-git':
        if name == 'go' and not version: (root / 'stage-complete').touch()
        selected = head and (root / 'stage-complete').exists()
    if selected and not ready.exists():
        child = subprocess.Popen([sys.executable, __file__, '--descendant'])
        while not ready.exists(): time.sleep(0.01)
        code = {exit_code!r}
        if code is None: child.wait(timeout=35)
        elif code < 0: os.kill(os.getpid(), -code)
        elif code: raise SystemExit(code)
    if version: print('go version go1.27.1 darwin/arm64')
    if head: print('frozen-scratch-head')
'''
    for name in ('go', 'git'):
        path = tools / name
        path.write_text(script)
        path.chmod(0o755)
    env = dict(os.environ, PATH=f'{tools}:{Path(sys.executable).parent}:/usr/bin:/bin')
    if phase == 'launch':
        # Deliver a real signal after Popen creates the child but before it
        # returns to the driver's ownership scope. No production hook needed.
        launcher = root / 'launch-window.py'
        launcher.write_text(f'''import os, pathlib, runpy, signal, subprocess, sys, time
original = subprocess.Popen
def launch(args, *a, **kw):
    process = original(args, *a, **kw)
    if list(args) == ['go', 'mod', 'download']:
        deadline = time.monotonic() + 5
        while not pathlib.Path({str(ready)!r}).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        os.kill(os.getpid(), signal.SIGTERM)
    return process
subprocess.Popen = launch
sys.argv = [{str(root / 'scripts/ci.py')!r}, 'setup']
runpy.run_path(sys.argv[0], run_name='__main__')
''')
        return [sys.executable, str(launcher)], env, ready, terminated
    return [str(root / 'bin/ci'), 'setup'], env, ready, terminated


def cleanup_cli(process, ready):
    info = json.loads(ready.read_text()) if ready.exists() else {}
    for group in {process.pid, info.get('group', process.pid)}:
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
    process.wait(timeout=5)


class CancellationTests(unittest.TestCase):
    def test_cli_signals_drain_before_releasing_state(self):
        self.assert_cancellation([('stage', signum) for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)])

    def test_captured_commands_are_cancelled_and_drained(self):
        self.assert_cancellation([(phase, signal.SIGTERM) for phase in ('version', 'git')])

    def test_signal_during_spawn_still_drains_new_child(self):
        self.assert_cancellation([('launch', signal.SIGTERM)])

    def test_final_source_inspection_can_be_cancelled(self):
        self.assert_cancellation([('final-git', signal.SIGHUP)])

    def assert_cancellation(self, cases):
        for phase, signum in cases:
            with self.subTest(phase=phase, signal=signum), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                command, env, ready, terminated = descendant_cli(root, phase)
                with (root / 'cli.log').open('w') as log:
                    process = subprocess.Popen(command, env=env, stdout=log, stderr=log, start_new_session=True)
                    try:
                        deadline = time.monotonic() + 10
                        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(ready.exists(), (root / 'cli.log').read_text())
                        info = json.loads(ready.read_text())
                        if phase != 'launch':
                            process.send_signal(signum)
                        deadline = time.monotonic() + 5
                        while not terminated.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(terminated.exists(), 'signal did not reach owned descendant')
                        self.assertTrue(Path(info['home']).is_dir())
                        with (root / '.ci/run.lock').open('a') as lock:
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.assertEqual(process.wait(timeout=10), 128 + signum)
                        self.assertFalse(live(info['leader']))
                        self.assertFalse(live(info['child']))
                        if phase not in ('git', 'final-git'):
                            self.assertFalse(Path(info['home']).exists())
                        with (root / '.ci/run.lock').open('a') as lock:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    finally:
                        cleanup_cli(process, ready)

    def test_exited_leaders_are_drained_and_status_preserved(self):
        for phase in ('stage', 'version'):
            for code in (0, 23, -signal.SIGTERM):
                with self.subTest(phase=phase, code=code), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    command, env, ready, _ = descendant_cli(root, phase, code)
                    with (root / 'cli.log').open('w') as log:
                        process = subprocess.Popen(command, env=env, stdout=log, stderr=log, start_new_session=True)
                        try:
                            self.assertEqual(process.wait(timeout=12), code if code >= 0 else 128 - code,
                                             (root / 'cli.log').read_text())
                            info = json.loads(ready.read_text())
                            self.assertFalse(live(info['leader']))
                            self.assertFalse(live(info['child']))
                            self.assertFalse(Path(info['home']).exists())
                        finally:
                            cleanup_cli(process, ready)


class PortableTests(unittest.TestCase):
    def test_private_environment(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'private', 'GOFLAGS': '-mod=mod', 'TAILSCALE_AUTHKEY': 'private'}):
            env = ci.environment(Path('/test/home'))
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('TAILSCALE_AUTHKEY', env)
        self.assertEqual(env['GOFLAGS'], '-mod=readonly')
        self.assertEqual(env['GOENV'], 'off')
        self.assertEqual(env['HOME'], '/test/home')

    def test_failed_command_propagates(self):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            ci.run([sys.executable, '-c', 'raise SystemExit(17)'], ci.environment(ci.ROOT / '.ci'))
        self.assertEqual(caught.exception.returncode, 17)

    def test_exited_leader_descendant_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / 'pid'
            code = 'import subprocess, pathlib, sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"]); pathlib.Path(sys.argv[1]).write_text(str(p.pid))'
            ci.run([sys.executable, '-c', code, str(pidfile)], ci.environment(Path(tmp)))
            pid = int(pidfile.read_text())
            status = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not status or status.startswith('Z'), status)

    def test_source_guard_detects_further_dirty_edit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ci, 'ROOT', Path(tmp)):
            subprocess.run(['git', 'init', '-q', tmp], check=True)
            path = Path(tmp) / 'source'
            path.write_text('initial')
            subprocess.run(['git', '-C', tmp, 'add', 'source'], check=True)
            subprocess.run(['git', '-C', tmp, '-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            path.write_text('already dirty')
            with self.assertRaisesRegex(RuntimeError, 'changed source'):
                with ci.checkout():
                    path.write_text('further edit')
            self.assertEqual(path.read_text(), 'further edit')

    def test_same_worktree_lock_rejects(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ci, 'ROOT', Path(tmp)), patch.object(ci, 'snapshot', return_value='unchanged'):
            with ci.checkout():
                with self.assertRaisesRegex(RuntimeError, 'Another CI'):
                    with ci.checkout():
                        self.fail('lock not enforced')

    def test_make_clean_preserves_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            for name in ('check', 'doctor', 'ci', 'ferry', 'ferryd'):
                (root / 'bin' / name).touch()
            (root / 'Makefile').write_bytes((ci.ROOT / 'Makefile').read_bytes())
            subprocess.run(['make', 'clean'], cwd=root, check=True, capture_output=True)
            for name in ('check', 'doctor', 'ci'):
                self.assertTrue((root / 'bin' / name).exists())
            self.assertFalse((root / 'bin/ferry').exists())
