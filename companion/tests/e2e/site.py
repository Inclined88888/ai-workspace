"""Local test website for the browser scenario: a home page, a contact form, and a thank-you page. Logs submissions."""
import html, json, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = sys.argv[1]
PAGES = {
 "/": "<title>Test Shop</title><h1>Test Shop</h1><a href='/contact'>Contact form</a> <a href='/about'>About us</a>",
 "/about": "<title>About</title><h1>About</h1><p>We sell test things.</p><a href='/'>Home</a>",
 "/contact": "<title>Contact</title><h1>Contact</h1><form method='post' action='/submit'><label>Name <input name='name'></label>"
             "<label>Email <input name='email' type='email'></label><label>Password <input name='pw' type='password'></label>"
             "<button type='submit'>Submit</button></form>",
}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, code=200):
        b = f"<!doctype html><html><body>{body}</body></html>".encode()
        self.send_response(code); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"User-agent: *\nAllow: /\n"); return
        self._send(PAGES.get(self.path, "<h1>Not found</h1>"), 200 if self.path in PAGES else 404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        with open(LOG, "a") as f: f.write(json.dumps(form) + "\n")
        self._send(f"<title>Thanks</title><h1>Thanks, {html.escape(form.get('name', ''))}</h1><p>We got your message.</p>")
ThreadingHTTPServer(("127.0.0.1", 8897), H).serve_forever()
