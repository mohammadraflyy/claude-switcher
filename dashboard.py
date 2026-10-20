"""Local dashboard on 127.0.0.1 (opened from the tray). Manage saved Claude accounts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import switcher

PORT = 8766

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Claude Switcher</title>
<style>
:root{--bg:#faf9f5;--card:#fff;--fg:#1f1e1d;--mute:#73726c;--line:#e5e3da;--acc:#d97757;--ok:#2e7d4f}
@media(prefers-color-scheme:dark){:root{--bg:#1f1e1d;--card:#2b2a28;--fg:#f0eee6;--mute:#a09f97;--line:#3d3b37;--ok:#6cc790}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:32px 16px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px;display:flex;gap:12px;align-items:center}
.card.active{border-color:var(--ok)}.grow{flex:1;min-width:0}.mail{font-weight:600;overflow-wrap:anywhere}
.meta{color:var(--mute);font-size:13px}.badge{color:var(--ok);font-size:12px;font-weight:600}
button{font:inherit;border:1px solid var(--line);background:none;color:var(--fg);border-radius:8px;padding:6px 12px;cursor:pointer}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}button:hover{opacity:.85}
#msg{min-height:24px;margin:12px 0;color:var(--mute)}.err{color:#c0392b}
.note{border:1px dashed var(--acc);border-radius:12px;padding:12px 16px;margin-bottom:16px}
@media(max-width:480px){.card{flex-wrap:wrap}}
</style>
<main>
<h1>Claude Switcher</h1>
<p class=sub>Saved accounts. New <code>claude</code> sessions use the active one; running sessions keep theirs.</p>
<div id=warn></div><div id=note></div><div id=list></div><div id=msg></div>
<p class=meta>Log in to another account with <code>claude /login</code>, then press <b>Save current login</b>. Use only your own accounts.</p>
</main>
<script>
const $=id=>document.getElementById(id);let sessions=0;
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const say=(t,bad)=>{$('msg').textContent=t;$('msg').className=bad?'err':''};
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
async function act(path,body,ok){try{await post(path,body);say(ok);}catch(e){say(e.message,1)}load()}
async function load(){
  const j=await (await fetch('/api/accounts')).json();
  sessions=j.sessions;
  $('warn').replaceChildren();
  if(sessions)$('warn').append(el('div','note',sessions+' claude session'+(sessions>1?'s are':' is')+' running. Sessions keep the account they started with; open a new session after switching.'));
  $('note').replaceChildren();
  if(j.unsaved){const n=el('div','note');n.append(el('div','',j.unsaved+' is logged in but not saved yet. '));
    const b=el('button','pri','Save current login');b.onclick=()=>act('/api/save',0,'Saved.');n.append(b);$('note').append(n)}
  $('list').replaceChildren(...j.accounts.map(a=>{
    const c=el('div','card'+(a.active?' active':'')),g=el('div','grow');
    g.append(el('div','mail',a.email));
    g.append(el('div','meta',[a.name,a.org,a.plan].filter(Boolean).join(' · ')));
    c.append(g);
    if(a.active)c.append(el('span','badge','ACTIVE'));
    else{const s=el('button','pri','Switch');s.onclick=()=>{
      if(sessions&&!confirm(sessions+' running claude session(s) will keep their current account. Switch anyway?'))return;
      act('/api/switch',{uuid:a.uuid},'Switched to '+a.email+'. Start a new claude session.')};c.append(s)}
    const d=el('button','','Remove');d.onclick=()=>{if(confirm('Remove '+a.email+' from the switcher? You stay logged in if active.'))act('/api/remove',{uuid:a.uuid},'Removed.')};
    c.append(d);return c}));
  if(!j.accounts.length&&!j.unsaved)$('list').append(el('p','meta','No accounts yet. Run claude /login first.'))}
load();setInterval(load,5000);
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _trusted(self):
        """Block DNS rebinding and cross-site requests to this local server."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (origin is None or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        if not self._trusted():
            return self._send(403, {"error": "forbidden"})
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html")
        if self.path == "/api/accounts":
            accounts, unsaved = switcher.details()
            return self._send(200, {"accounts": accounts, "unsaved": unsaved, "sessions": switcher.running_sessions()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._trusted() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/save":
                switcher.save_current()
            elif self.path == "/api/switch":
                switcher.switch(body["uuid"])
            elif self.path == "/api/remove":
                switcher.remove(body["uuid"])
            else:
                return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(400, {"error": str(e) or type(e).__name__})
        self._send(200, {"ok": True})


def start(port=PORT):
    """Serve in a daemon thread; tries the next few ports if busy. Returns (server, url)."""
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    else:
        raise OSError("no free port")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"
