"""Exercise deployment failure handling without a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project with spaces'
        (self.project / 'scripts').mkdir(parents=True)
        shutil.copy(ROOT / 'scripts/deploy.sh', self.project / 'scripts/deploy.sh')
        shutil.copy(ROOT / 'compose.ghcr.yaml', self.project / 'compose.ghcr.yaml')
        self.bin = Path(self.temp.name) / 'bin'
        self.bin.mkdir()
        self.log = Path(self.temp.name) / 'docker.jsonl'
        docker = self.bin / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['DOCKER_TEST_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if '--images' in args:
    image = os.environ.get('KLOP_IMAGE')
    if not image and '--env-file' in args:
        for line in Path(args[args.index('--env-file') + 1]).read_text().splitlines():
            if line.startswith('KLOP_IMAGE='):
                image = line.split('=', 1)[1]
    if not image:
        sys.exit(1)
    print(image)
if 'pull' in args and os.environ.get('DOCKER_TEST_FAIL_PULL'):
    sys.exit(17)
if 'up' in args and os.environ.get('DOCKER_TEST_FAIL_UP'):
    sys.exit(18)
''')
        docker.chmod(0o755)
        self.env = {k:v for k,v in os.environ.items() if k not in ('KLOP_IMAGE', 'KLOP_ENV_FILE')}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ['PATH'], DOCKER_TEST_LOG=str(self.log))

    def deploy(self, *args, **env):
        return subprocess.run(['bash', str(self.project / 'scripts/deploy.sh'), *args],
                              env={**self.env, **env}, cwd='/', capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_pull_precedes_recreate_and_volume_is_preserved(self):
        result = self.deploy('ghcr.io/example/klop:latest')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        pull = next(i for i, args in enumerate(calls) if 'pull' in args)
        up = next(i for i, args in enumerate(calls) if 'up' in args)
        self.assertLess(pull, up)
        self.assertIn('--wait', calls[up])
        self.assertIn('--no-build', calls[up])
        self.assertIn('--project-directory', calls[up])
        self.assertIn(str(self.project), calls[up])
        self.assertEqual(calls[pull][-1], 'klop')
        self.assertFalse(any(word in args for args in calls for word in ('down', 'rm', 'prune', '--volumes')))

    def test_failed_pull_leaves_running_container_alone(self):
        result = self.deploy('ghcr.io/example/klop:latest', DOCKER_TEST_FAIL_PULL='1')
        self.assertEqual(result.returncode, 17)
        self.assertFalse(any('up' in args for args in self.calls()))

    def test_failed_readiness_is_not_reported_as_success(self):
        result = self.deploy('ghcr.io/example/klop:latest', DOCKER_TEST_FAIL_UP='1')
        self.assertEqual(result.returncode, 18)
        self.assertNotIn('Готово.', result.stdout)

    def test_reads_environment_file_without_executing_shell(self):
        marker = self.project / 'must-not-exist'
        (self.project / '.env').write_text(f'KLOP_IMAGE=ghcr.io/example/klop:latest\nUNUSED=$(touch "{marker}")\n')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertTrue(any('--env-file' in args for args in self.calls()))

    def test_rejects_other_registries_before_pull(self):
        result = self.deploy('docker.io/example/klop:latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('pull' in args or 'up' in args for args in self.calls()))

    def test_explicit_environment_file(self):
        env_file = self.project / 'production.env'
        env_file.write_text('KLOP_IMAGE=ghcr.io/example/klop:latest\n')
        result = self.deploy(KLOP_ENV_FILE=str(env_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(str(env_file) in args for args in self.calls()))


if __name__ == '__main__':
    unittest.main()
