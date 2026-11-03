// Same-origin rig for the web export: serves mobile/dist and forwards /api/* and /healthz to the backend on :8301.
const http = require('http');
const fs = require('fs');
const path = require('path');
const DIST = '/workspace/ai-workspace-v02a/mobile/dist';
const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.ttf': 'font/ttf', '.ico': 'image/x-icon', '.json': 'application/json' };
http.createServer((req, res) => {
  if (req.url.startsWith('/api/') || req.url === '/healthz') {
    const p = http.request({ host: '127.0.0.1', port: 8940, path: req.url, method: req.method, headers: req.headers }, (r) => {
      res.writeHead(r.statusCode, r.headers);
      r.pipe(res);
    });
    p.on('error', () => { res.writeHead(502); res.end(); });
    req.pipe(p);
    return;
  }
  let f = path.join(DIST, decodeURIComponent(req.url.split('?')[0]));
  if (!f.startsWith(DIST) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(DIST, 'index.html');
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
}).listen(8945, '127.0.0.1', () => console.log('proxy on 8945'));
