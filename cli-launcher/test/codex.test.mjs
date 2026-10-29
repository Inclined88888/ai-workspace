import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const adapter = fileURLToPath(new URL('../codex.mjs', import.meta.url));
test('Codex adapter sanitizes auth and only forwards task output', t => {
  const dir = mkdtempSync(join(tmpdir(), 'codex-adapter-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, 'codex'), `#!/usr/bin/env node
if (!process.argv.includes('analytics.enabled=false') || !process.argv.includes('otel.metrics_exporter="none"')) process.exit(2);
if (process.argv.includes('status')) { console.error(process.env.FAKE_STATUS); process.exit(Number(process.env.FAKE_EXIT || 0)); }
console.error('private diagnostic');
let task = ''; for await (const chunk of process.stdin) task += chunk;
console.log(task);
`, { mode: 0o755 });
  const run = (mode, status, code = '0') => spawnSync(process.execPath, [adapter, mode], {
    env: { PATH: dir + ':' + process.env.PATH, FAKE_STATUS: status, FAKE_EXIT: code },
    input: 'task result', encoding: 'utf8', timeout: 5000,
  });
  for (const status of ['Logged in using API key: private', 'Not logged in', 'garbage']) {
    const r = run('auth', status); assert.equal(r.status, 1);
    assert.deepEqual(JSON.parse(r.stdout), { authenticated: false }); assert.equal(r.stderr, '');
  }
  assert.equal(run('auth', 'Logged in using ChatGPT').status, 0);
  assert.equal(run('auth', 'Logged in using ChatGPT', '1').status, 1);
  const r = run('run', ''); assert.equal(r.status, 0);
  assert.equal(r.stdout, 'task result\n'); assert.equal(r.stderr, '');
});
