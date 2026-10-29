// CLI-specific argument/status adapter. server.mjs remains unchanged.
import { spawn } from 'node:child_process';
const privacy = ['-c', 'analytics.enabled=false', '-c', 'feedback.enabled=false',
  '-c', 'otel.exporter="none"', '-c', 'otel.trace_exporter="none"',
  '-c', 'otel.metrics_exporter="none"'];
const auth = process.argv[2] === 'auth';
if (!auth && process.argv[2] !== 'run') process.exit(1);
const args = auth ? [...privacy, 'login', 'status'] : [...privacy, 'exec',
  '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '-'];
const child = spawn('codex', args, { shell: false,
  stdio: auth ? ['ignore', 'ignore', 'pipe'] : ['inherit', 'inherit', 'ignore'] });
let status = '';
let overflow = false;
if (auth) child.stderr.on('data', chunk => {
  if (Buffer.byteLength(status) + chunk.length > 8192) { overflow = true; child.kill(); }
  else status += chunk.toString('utf8');
});
child.on('error', () => { process.exitCode = 1; });
child.on('close', code => {
  if (auth) {
    // Only the CLI's documented status message is accepted, never API-key login.
    const authenticated = !overflow && code === 0 && status.split(/\r?\n/).includes('Logged in using ChatGPT');
    process.stdout.write(JSON.stringify({ authenticated }));
    process.exitCode = authenticated ? 0 : 1;
  } else process.exitCode = code === 0 ? 0 : 1;
});
