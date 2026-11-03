// node grok-history.js <out> <port> <tid> <cid>
const puppeteer = require("puppeteer-core");
const [, , out, port, tid, cid] = process.argv;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox", "--font-render-hinting=none"] });
  const p = await b.newPage();
  p.on("pageerror", (e) => console.log("pageerror", e.message));
  await p.setViewport({ width: 1440, height: 900 });
  await p.goto(`http://localhost:${port}/api/desktop/session?token=desk-token-1`); await sleep(800);
  await p.goto(`http://localhost:${port}/teammates/${tid}`); await sleep(1800);
  console.log("url", p.url());
  const r1 = await p.evaluate(() => ({ bubbles: [...document.querySelectorAll(".tm-bubble")].map((x) => x.textContent.slice(0, 40)), choices: [...document.querySelectorAll(".tm-choices button")].map((x) => x.textContent) }));
  console.log(JSON.stringify(r1));
  await p.reload(); await sleep(1800);
  const r2 = await p.evaluate(() => ({ bubbles: document.querySelectorAll(".tm-bubble").length, choices: document.querySelectorAll(".tm-choices button").length, url: location.search }));
  console.log("after reload", JSON.stringify(r2));
  await p.screenshot({ path: `${out}/12-history-and-choices.png` });
  const nb = (await p.$$("button")).filter(Boolean);
  for (const h of nb) { if ((await h.evaluate((e) => e.textContent)) === "New chat") { await h.click(); break; } }
  await sleep(600);
  console.log("new chat", await p.evaluate(() => [document.querySelectorAll(".tm-bubble").length, location.search]));
  await b.close();
})();
