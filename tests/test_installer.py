"""Exercise real Git installs against local fixtures; never start Docker."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.dest = self.root / 'installed'
        self.env = {'PATH': str(self.bin) + ':' + os.defpath, 'HOME': str(self.root),
                    'AI_WORKSPACE_REPO': str(self.source), 'AI_WORKSPACE_DIR': str(self.dest),
                    'AI_WORKSPACE_REF': 'main', 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        self.tool('docker', '#!/bin/sh\nexit 0\n')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Installer fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.source / '.gitignore').write_text('.env\n.rooks/\n')
        (self.source / 'setup.sh').write_text(
            '#!/usr/bin/env bash\nset -eu\numask 077\n'
            'mkdir -p .rooks\nprintf "%s\\n" "$@" > .rooks/args\n'
            'if [ ! -f .env ]; then printf "TEST_SECRET=fixture\\n" > .env; fi\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.sha = self.git('rev-parse', 'HEAD').stdout.strip()

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', '-C', str(cwd or self.source), *args],
                              env=self.env, text=True, capture_output=True, check=True)

    def run_install(self, *args, success=True):
        result = subprocess.run(['/bin/bash', str(ROOT / 'install.sh'), *args],
                                env=self.env, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_full_commit_pin_and_options(self):
        self.env['AI_WORKSPACE_REF'] = self.sha
        self.run_install('--with', 'search,browser')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.dest).stdout.strip(), self.sha)
        self.assertIn('search,browser', (self.dest / '.rooks/args').read_text())
        self.assertEqual((self.dest / '.env').stat().st_mode & 0o777, 0o600)

    def test_installed_code_is_readable_by_container_users(self):
        # Regression: an owner-only umask made the checkout mode 600, Docker copied that into the images, and the
        # non-root container users could not read ROOKS's own code (both containers restarted forever).
        self.run_install()
        self.assertEqual((self.dest / 'setup.sh').stat().st_mode & 0o044, 0o044)
        self.assertEqual((self.dest / '.env').stat().st_mode & 0o777, 0o600)
        for dockerfile in ('workbench/Dockerfile', 'workbench/runner/Dockerfile', 'browser-agent/Dockerfile'):
            self.assertIn('chmod -R a+rX', (ROOT / dockerfile).read_text(), dockerfile)
        self.assertIn('git ls-files -z | xargs -0 chmod go+r', (ROOT / 'setup.sh').read_text())

    def test_fast_forward_preserves_secret(self):
        self.run_install()
        (self.dest / '.env').write_text('KEEP-MY-SECRET\n')
        (self.source / 'new-file').write_text('updated')
        self.git('add', '.')
        self.git('commit', '-qm', 'update')
        self.run_install()
        self.assertEqual((self.dest / '.env').read_text(), 'KEEP-MY-SECRET\n')
        self.assertEqual((self.dest / 'new-file').read_text(), 'updated')

    def test_refuses_dirty_install(self):
        self.run_install()
        (self.dest / 'setup.sh').write_text('do not overwrite')
        self.run_install(success=False)
        self.assertEqual((self.dest / 'setup.sh').read_text(), 'do not overwrite')

    def test_refuses_wrong_origin(self):
        self.run_install()
        self.git('remote', 'set-url', 'origin', str(self.root / 'other'), cwd=self.dest)
        self.assertIn('different source', self.run_install(success=False).stderr)

    def test_refuses_archive_or_unrelated_directory(self):
        self.dest.mkdir()
        (self.dest / 'setup.sh').write_text('unrelated file')
        self.run_install(success=False)
        self.assertEqual((self.dest / 'setup.sh').read_text(), 'unrelated file')

    def test_refuses_symlink_destination(self):
        self.dest.symlink_to(self.source, target_is_directory=True)
        self.assertIn('symbolic-link', self.run_install(success=False).stderr)

    def test_failed_download_leaves_no_install(self):
        self.env['AI_WORKSPACE_REF'] = 'missing-ref'
        self.run_install(success=False)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob('.rooks-download.*')), [])

    def test_refuses_divergent_update(self):
        self.run_install()
        self.git('config', 'user.name', 'Installer fixture', cwd=self.dest)
        self.git('config', 'user.email', 'fixture@example.invalid', cwd=self.dest)
        (self.dest / 'local-file').write_text('keep')
        self.git('add', '.', cwd=self.dest)
        self.git('commit', '-qm', 'local work', cwd=self.dest)
        (self.source / 'remote-file').write_text('remote')
        self.git('add', '.')
        self.git('commit', '-qm', 'remote work')
        before = self.git('rev-parse', 'HEAD', cwd=self.dest).stdout
        self.run_install(success=False)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.dest).stdout, before)

    def test_rejects_unsupported_system(self):
        self.tool('uname', '#!/bin/sh\necho MINGW64_NT\n')
        self.assertIn('WSL2', self.run_install(success=False).stderr)
        self.assertFalse(self.dest.exists())

    def test_linux_and_wsl_platform_path(self):
        self.tool('uname', '#!/bin/sh\nif [ "$1" = "-s" ]; then echo Linux; else echo x86_64; fi\n')
        self.run_install()

    def test_docker_failure_does_not_download(self):
        self.tool('docker', '#!/bin/sh\nexit 1\n')
        self.run_install(success=False)
        self.assertFalse(self.dest.exists())

    def test_help_needs_no_docker(self):
        self.tool('docker', '#!/bin/sh\nexit 1\n')
        self.assertIn('Compose v2', self.run_install('--help').stdout)

    def test_rejects_option_in_ref(self):
        self.env['AI_WORKSPACE_REF'] = '--upload-pack=anything'
        self.run_install(success=False)
        self.assertFalse(self.dest.exists())

    def test_truncated_script_does_not_start_install(self):
        body = (ROOT / 'install.sh').read_text().rsplit('main "$@"', 1)[0]
        result = subprocess.run(['/bin/bash'], input=body, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())

    def real_setup(self):
        (self.source / 'setup.sh').write_text((ROOT / 'setup.sh').read_text())
        (self.source / '.env.example').write_text((ROOT / '.env.example').read_text())
        self.tool('curl', '#!/bin/sh\nexit 1\n')
        return subprocess.run(['/bin/bash', str(self.source / 'setup.sh'), '--yes'],
                              env=self.env, text=True, capture_output=True)

    def test_setup_restricts_existing_env_permissions(self):
        path = self.source / '.env'
        path.write_text('APP_SECRET=installer-fixture-secret\n')
        path.chmod(0o644)
        result = self.real_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn('APP_SECRET=installer-fixture-secret\n', path.read_text())

    def test_setup_refuses_env_symlink(self):
        outside = self.root / 'outside'
        outside.write_text('must remain untouched')
        (self.source / '.env').symlink_to(outside)
        result = self.real_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('symbolic-link', result.stderr)
        self.assertEqual(outside.read_text(), 'must remain untouched')


if __name__ == '__main__':
    unittest.main()
