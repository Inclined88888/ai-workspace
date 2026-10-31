// node grok-shots.js <outdir> <port>
const puppeteer = require("puppeteer-core");
const [, , out, port] = process.argv;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox", "--font-render-hinting=none"] });
  const page = await browser.newPage();
  page.on("pageerror", (e) => console.log("pageerror", e.message));
  page.on("console", (m) => { if (m.type() === "error") console.log("console", m.text()); });
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(`http://localhost:${port}/api/desktop/session?token=desk-token-1`);
  await sleep(1200);
  const seed = await page.evaluate(async () => {
    const h = { "X-Workbench": "1", "Content-Type": "application/json" };
    const j = (p, b, m = "POST") => fetch("/api" + p, { method: m, headers: h, body: JSON.stringify(b) }).then((r) => r.json());
    await fetch("/api/desktop/welcome-done", { method: "POST", headers: h });
    const list = await fetch("/api/teammates").then((r) => r.json());
    if (list.teammates.length > 1) return { list };
    const trade = await j("/teammates", { template: "trade", name: "Office manager" });
    const study = await j("/teammates", { template: "study", name: "Study buddy" });
    const res = await j("/teammates", { template: "research", name: "Research desk" });
    await j(`/teammates/${trade.id}/notes`, { content: "Quotes go out on the Bunnings trade account" });
    await j(`/teammates/${trade.id}/notes`, { content: "Prefers Friday invoice runs" });
    await j(`/teammates/${trade.id}/routines`, { text: "Every weekday at 8am, summarise new job enquiries" });
    await j("/groups", { name: "Kitchen reno", members: [trade.id, res.id] });
    await j("/vault", { kind: "login", label: "Supplier portal", values: { site: "https://trade.example.com", username: "jo@sunsetplumbing.au", password: "s3cret-pass-123" } });
    await j("/vault", { kind: "card", label: "Business Visa", values: { name: "Jo Rivera", number: "4242 4242 4242 4242", expiry: "12/29", cvc: "123" } });
    await j("/pages", { title: "Weekly jobs report", body: "# Weekly jobs\n\nThree quotes out, two jobs booked.\n\n| Job | Suburb | Due |\n|---|---|---|\n| SS-42 | Paddington | Fri |\n| SS-43 | New Farm | Mon |\n\n- Chase the Ascot invoice\n- Order tapware for SS-43" });
    await j("/outbox", { channel: "email", recipients: "jane@example.com", subject: "Your quote", body: "Hi Jane,\n\nQuote attached for the kitchen tapware.\n\nThanks, Jo" });
    return { trade, study, res };
  });
  const list = await page.evaluate(() => fetch("/api/teammates").then((r) => r.json()));
  const trade = list.teammates.find((t) => t.name === "Office manager");
  const gid = list.groups[0]?.id;
  const setTheme = (t) => page.evaluate((t) => { localStorage.setItem("wb-appearance", JSON.stringify({ theme: t, font: "modern", size: 14, density: "comfortable", motion: "full", accent: "theme" })); localStorage.setItem("wb-nav-more", "1"); }, t);
  const shots = [["teammates", `/teammates/${trade.id}`, ["sunshine", "neon"]], ["new-teammate", "/teammates/new", ["sunshine"]],
    ["group", `/groups/${gid}`, ["sunshine"]], ["vault", "/vault", ["sunshine"]], ["pages", "/pages", ["sunshine"]], ["channels", "/channels", ["sunshine"]]];
  for (const [name, path, themes] of shots) for (const t of themes) {
    await setTheme(t);
    await page.goto(`http://localhost:${port}${path}`, { waitUntil: "networkidle0" });
    await sleep(1200);
    if (name === "pages") { await page.evaluate(() => document.querySelector(".tm-side .tm-person")?.click()); await sleep(1200); }
    if (name === "teammates") {
      // show the draft-before-send and secure-form cards as they appear in chat
      await page.evaluate(() => {});
    }
    await page.screenshot({ path: `${out}/${name}-${t}.png` });
    console.log("shot", name, t);
  }
  await browser.close();
})();
