"""``GET /login``: one self-contained page (inline CSS and JS, no other assets) and its
hash-based Content-Security-Policy (ADR 0003 item 4)."""

from __future__ import annotations

import base64
import hashlib

CSS = """
body{font:16px system-ui,sans-serif;background:#111;color:#eee;margin:0;display:flex;
min-height:100vh;align-items:center;justify-content:center}
form{background:#1c1c1c;padding:24px;border-radius:10px;width:min(420px,92vw)}
h1{font-size:20px;margin:0 0 12px}p{color:#aaa;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px;margin:8px 0 12px;border-radius:6px;
border:1px solid #444;background:#000;color:#eee}
button{padding:10px 16px;border:0;border-radius:6px;background:#1db954;color:#000;
font-weight:600;cursor:pointer}#msg{color:#f77;min-height:1.2em}
"""

JS = """
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("msg");
  msg.textContent = "";
  try {
    const r = await fetch("/login", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Spotdown-UI": "1" },
      body: JSON.stringify({ token: document.getElementById("t").value.trim() }),
    });
    if (r.status === 204) { location.assign("/"); return; }
    msg.textContent = r.status === 429 ? "Too many attempts; wait a minute."
      : r.status === 401 ? "Wrong token." : "Login failed (HTTP " + r.status + ").";
  } catch (err) { msg.textContent = "Network error."; }
});
"""

HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>spotdown: sign in</title><style>{CSS}</style></head>
<body><form id="f"><h1>spotdown</h1>
<p>Paste the access token from the file the server console names
(<code>data/standalone_token</code> by default).</p>
<label for="t">Access token</label>
<input id="t" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button><div id="msg" role="alert"></div></form>
<script>{JS}</script></body></html>
"""


def _sha256(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


CSP = (
    f"default-src 'none'; style-src '{_sha256(CSS)}'; script-src '{_sha256(JS)}'; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'"
)

HEADERS = [
    (b"content-type", b"text/html; charset=utf-8"),
    (b"content-security-policy", CSP.encode("ascii")),
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]
BODY = HTML.encode("utf-8")
