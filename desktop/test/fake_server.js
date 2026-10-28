"use strict";
// A stand-in ROOKS server for the supervisor tests: answers /healthz with 200 on the given port.
// FAKE_DIE_AFTER_MS makes it exit by itself (a crash); FAKE_NEVER_HEALTHY makes it run but never answer 200.
const http = require("node:http");
const port = Number(process.argv[2]);
http.createServer((req, res) => {
  res.writeHead(process.env.FAKE_NEVER_HEALTHY ? 503 : req.url === "/healthz" ? 200 : 404);
  res.end("ok");
}).listen(port, "127.0.0.1");
if (process.env.FAKE_DIE_AFTER_MS) setTimeout(() => process.exit(3), Number(process.env.FAKE_DIE_AFTER_MS));
