// Official CLI subprocess transport. No SDKs, credential-file access, or content logs.
import http from 'node:http';
import { spawn } from 'node:child_process';
import { timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export function createLauncher(config) {
  const { command, args, authArgs, authMatch, token, cwd, env,
    timeoutMs = 120000, maxOutputBytes = 1048576, killGraceMs = 1000 } = config;
  if (typeof token !== 'string' || token.length < 24) throw new Error('Launcher key required');
  if (!command || !Array.isArray(args) || !Array.isArray(authArgs) ||
      !authMatch || typeof authMatch !== 'object' || !Object.keys(authMatch).length) {
    throw new Error('Launcher command configuration required');
  }
  let busy = false;
  let stopping = false;
  const active = new Set();
  const expected = Buffer.from(`Bearer ${token}`);

  function execute(argv, input, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(new Error('cancelled'));
      let failed = false;
      let bytes = 0;
      const chunks = [];
      // Detached process groups let cancellation also reap CLI helpers on Linux.
      const child = spawn(command, argv, { cwd, env, shell: false,
        detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'] });
      let escalation;
      let timer;
      function kill(sig) {
        try {
          if (process.platform === 'win32') child.kill(sig);
          else process.kill(-child.pid, sig);
        } catch { /* already exited */ }
      }
      function terminate() {
        if (failed) return;
        failed = true;
        kill('SIGTERM');
        escalation = setTimeout(() => kill('SIGKILL'), killGraceMs);
      }
      active.add(terminate);
      signal.addEventListener('abort', terminate, { once: true });
      timer = setTimeout(terminate, timeoutMs);
      function consume(chunk, keep) {
        bytes += chunk.length;
        if (bytes > maxOutputBytes) terminate();
        else if (keep) chunks.push(chunk);
      }
      child.stdout.on('data', chunk => consume(chunk, true));
      // Drain stderr without exposing CLI diagnostics (may contain secrets).
      child.stderr.on('data', chunk => consume(chunk, false));
      child.stdin.on('error', () => {}); // EPIPE is reported by exit status.
      child.once('error', () => { failed = true; });
      child.once('close', (code, exitSignal) => {
        clearTimeout(timer);
        clearTimeout(escalation);
        // A successful parent can still leave helpers holding resources.
        kill('SIGKILL');
        active.delete(terminate);
        signal.removeEventListener('abort', terminate);
        if (failed || code !== 0 || exitSignal) reject(new Error('CLI failed'));
        else resolve(Buffer.concat(chunks).toString('utf8'));
      });
      child.stdin.end(input);
    });
  }

  async function signedIn(signal) {
    try {
      const status = JSON.parse(await execute(authArgs, '', signal));
      return Object.entries(authMatch).every(([key, value]) => status[key] === value);
    } catch { return false; }
  }
  function reply(res, status, data) {
    if (!res.destroyed) res.writeHead(status, {
      'Content-Type': 'application/json', 'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    }).end(JSON.stringify(data));
  }
  const server = http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') return reply(res, 200, { ok: true });
    const supplied = Buffer.from(req.headers.authorization || '');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      return reply(res, 401, { error: 'Unauthorized' });
    }
    if (!((req.method === 'GET' && req.url === '/auth/status') ||
          (req.method === 'POST' && req.url === '/run'))) return reply(res, 404, { error: 'Not found' });
    if (busy || stopping) return reply(res, 503, { error: 'Launcher unavailable' });
    busy = true;
    const controller = new AbortController();
    const abort = () => controller.abort();
    req.once('aborted', abort);
    res.once('close', abort);
    try {
      if (req.method === 'GET') {
        return reply(res, 200, { authenticated: await signedIn(controller.signal) });
      }
      if (req.headers['content-type']?.split(';')[0].trim() !== 'application/json') {
        return reply(res, 415, { error: 'Expected application/json' });
      }
      let bytes = 0;
      const body = [];
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 65536) { reply(res, 413, { error: 'Task too large' }); return; }
        body.push(chunk);
      }
      let task;
      try {
        const parsed = JSON.parse(Buffer.concat(body).toString('utf8'));
        task = parsed.task;
        if (typeof task !== 'string' || !task.trim() || Object.keys(parsed).some(k => k !== 'task')) {
          throw new Error();
        }
      } catch { return reply(res, 400, { error: 'Expected a nonempty task string only' }); }
      if (!await signedIn(controller.signal)) return reply(res, 409, { error: 'Sign in using the official CLI' });
      const output = await execute(args, task, controller.signal);
      reply(res, 200, { output });
    } catch { reply(res, 502, { error: 'CLI failed, cancelled, or exceeded its limits' }); }
    finally {
      busy = false;
      req.removeListener('aborted', abort);
      res.removeListener('close', abort);
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  server.maxRequestsPerSocket = 100;
  return { server, async stop() {
    stopping = true;
    for (const terminate of active) terminate();
    server.closeIdleConnections();
    await new Promise(resolve => server.close(resolve));
  } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    // Deliberately exclude inherited API keys, bearer tokens and provider overrides.
    const env = {};
    for (const key of ['PATH', 'HOME', 'LANG', 'TMPDIR', 'CLAUDE_CONFIG_DIR']) {
      if (process.env[key]) env[key] = process.env[key];
    }
    Object.assign(env, JSON.parse(process.env.LAUNCHER_CHILD_ENV || '{}'));
    const launcher = createLauncher({
      command: process.env.LAUNCHER_COMMAND,
      args: JSON.parse(process.env.LAUNCHER_ARGS || '[]'),
      authArgs: JSON.parse(process.env.LAUNCHER_AUTH_ARGS || '[]'),
      authMatch: JSON.parse(process.env.LAUNCHER_AUTH_MATCH || '{}'),
      token: process.env.LAUNCHER_API_KEY,
      cwd: process.env.LAUNCHER_CWD || '/workspace', env,
    });
    launcher.server.listen(8110, '0.0.0.0');
    for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void launcher.stop(); });
  } catch { process.stderr.write('Invalid launcher configuration\n'); process.exitCode = 1; }
}
