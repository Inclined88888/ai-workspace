import { spawn } from 'node:child_process';
if (process.argv[2] === 'auth') {
  console.log(JSON.stringify({ loggedIn: process.env.AUTH !== 'out',
    authMethod: process.env.AUTH === 'api' ? 'api_key' : 'claude.ai', email: 'private@example.com' }));
  process.exit(process.env.AUTH === 'fail' ? 1 : 0);
}
let task = '';
for await (const chunk of process.stdin) task += chunk;
if (task === 'fail') { console.error('secret-token-diagnostic'); process.exit(1); }
if (task === 'overflow') { console.log('x'.repeat(2048)); }
else if (task === 'hang') {
  process.on('SIGTERM', () => {});
  setInterval(() => {}, 100);
} else if (task === 'tree') {
  const helper = spawn(process.execPath, ['-e', 'setInterval(()=>{},100)'], { stdio: 'inherit' });
  console.log(helper.pid);
  setInterval(() => {}, 100);
} else console.log(JSON.stringify({ task, args: process.argv.slice(2), telemetry: process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC }));
