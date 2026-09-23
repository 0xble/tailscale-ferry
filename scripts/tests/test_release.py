"""Exercise release guards using inert gh/GoReleaser fixtures, never publication."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        (self.root / 'bin').mkdir(parents=True)
        shutil.copy2(ROOT / 'bin/release', self.root / 'bin/release')
        (self.root / 'bin/ci').write_text('#!/bin/sh\nexit "${FIXTURE_CI_EXIT:-0}"\n')
        (self.root / 'bin/ci').chmod(0o755)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=CI', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture')
        self.git('tag', 'v1.2.3')
        self.git('remote', 'add', 'origin', 'git@github.com:0xble/tailscale-ferry.git')
        self.head = self.git('rev-parse', 'HEAD').strip()
        tools = Path(self.tmp.name) / 'tools'
        tools.mkdir()
        for name, content in {
            'gh': '#!/bin/sh\nprintf "%s\\n" "$FIXTURE_REMOTE_HEAD"\n',
            'goreleaser': '#!/bin/sh\nif [ "$1" = --version ]; then echo "GitVersion:    2.17.1"; else printf "%s\\n" "$*" > "$FIXTURE_RELEASE_ARGS"; fi\n',
        }.items():
            (tools / name).write_text(content)
            (tools / name).chmod(0o755)
        self.output = Path(self.tmp.name) / 'release-args'
        self.env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'],
                    'FIXTURE_REMOTE_HEAD': self.head, 'FIXTURE_RELEASE_ARGS': str(self.output)}

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def release(self):
        return subprocess.run([str(self.root / 'bin/release'), '--draft', 'v1.2.3'], env=self.env, capture_output=True, text=True)

    def test_exact_clean_candidate_invokes_draft_only(self):
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text().strip(), 'release --clean --draft')

    def test_remote_mismatch_refuses_release(self):
        self.env['FIXTURE_REMOTE_HEAD'] = '0' * 40
        result = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Remote tag differs', result.stderr)
        self.assertFalse(self.output.exists())

    def test_gate_failure_refuses_release(self):
        self.env['FIXTURE_CI_EXIT'] = '17'
        self.assertEqual(self.release().returncode, 17)
        self.assertFalse(self.output.exists())

    def test_dirty_source_refuses_release(self):
        (self.root / 'dirty').touch()
        self.assertIn('clean checkout', self.release().stderr)
        self.assertFalse(self.output.exists())
