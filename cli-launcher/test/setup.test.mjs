import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, copyFileSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../', import.meta.url));
test('setup enables, preserves secrets, then disables the optional launcher', t => {
  const dir = mkdtempSync(join(tmpdir(), 'launcher-setup-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const path of ['setup.sh', '.env.example']) copyFileSync(join(root, path), join(dir, path));
  mkdirSync(join(dir, 'bin'));
  // No real Docker or outbound requests: exercise the actual guided setup wiring.
  writeFileSync(join(dir, 'bin/docker'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  writeFileSync(join(dir, 'bin/curl'), '#!/bin/sh\nexit 1\n', { mode: 0o755 });
  const run = addon => {
    const result = spawnSync('bash', ['setup.sh', '--yes', '--with', addon], {
      cwd: dir, env: { ...process.env, PATH: join(dir, 'bin') + ':' + process.env.PATH }, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout;
  };
  const output = run('claude-code');
  let env = readFileSync(join(dir, '.env'), 'utf8');
  assert.match(env, /^COMPOSE_PROFILES=claude-code$/m);
  assert.match(env, /^CLAUDE_CODE_URL=http:\/\/claude-code:8110$/m);
  const key = env.match(/^CLAUDE_CODE_API_KEY=(.+)$/m)?.[1];
  assert.ok(key?.length >= 24);
  assert.ok(!output.includes(key));
  run('claude-code');
  assert.ok(readFileSync(join(dir, '.env'), 'utf8').includes(`CLAUDE_CODE_API_KEY=${key}`));
  run('none');
  env = readFileSync(join(dir, '.env'), 'utf8');
  assert.match(env, /^COMPOSE_PROFILES=$/m);
  assert.match(env, /^CLAUDE_CODE_URL=$/m);
  assert.ok(env.includes(`CLAUDE_CODE_API_KEY=${key}`));
});
