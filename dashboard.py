"""Local dashboard on 127.0.0.1 (opened from the tray). Manage saved Claude accounts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import switcher
import updates
import usage

PORT = 8766

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Claude Switcher</title><link rel=icon href=data:,>
<style>
:root{--bg:#faf9f5;--card:#fff;--fg:#1f1e1d;--mute:#73726c;--line:#e5e3da;--acc:#d97757;--ok:#2e7d4f;--bad:#c0392b}
@media(prefers-color-scheme:dark){:root{--bg:#1f1e1d;--card:#2b2a28;--fg:#f0eee6;--mute:#a09f97;--line:#3d3b37;--ok:#6cc790;--bad:#ff8a7a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:32px 16px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px;display:flex;gap:12px;align-items:center}
.card.active{border-color:var(--ok)}.grow{flex:1;min-width:0}.mail{font-weight:600;overflow-wrap:anywhere}
.meta{color:var(--mute);font-size:13px}.badge{color:var(--ok);font-size:12px;font-weight:600}
button{font:inherit;border:1px solid var(--line);background:none;color:var(--fg);border-radius:8px;padding:6px 12px;cursor:pointer}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}button:hover{opacity:.85}
#msg{min-height:24px;margin:12px 0;color:var(--mute)}.err{color:var(--bad)}
.note{border:1px dashed var(--acc);border-radius:12px;padding:12px 16px;margin-bottom:16px}
.dot{width:12px;height:12px;border-radius:50%;flex:none;background:var(--line)}.lbl{font-size:12px;font-weight:600;color:var(--mute);margin-left:6px}
.exp{color:var(--bad);font-size:12px;font-weight:600}.sw{display:inline-block;width:22px;height:22px;border-radius:50%;margin:4px;cursor:pointer;border:2px solid transparent}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
@media(max-width:480px){.card{flex-wrap:wrap}.grow{flex-basis:calc(100% - 24px)}}
</style>
<main>
<h1>Claude Switcher</h1>
<p class=sub>Saved accounts. New <code>claude</code> sessions use the active one; a running session may keep its own. <b>Switch &amp; resume</b> continues your latest chat on the new account.</p>
<div id=warn></div><div id=note></div><div id=list></div><div id=msg></div>
<p class=meta id=hint></p>
<div class=row><a href=/api/backup download><button>Backup</button></a>
<button id=restore>Restore…</button><input id=file type=file accept=.json hidden></div>
<p class=meta>Backups stay encrypted and restore only on this Windows user. Log in to another account with <code>claude /login</code>, then press <b>Save current login</b>. Use only your own accounts.</p>
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
  if(j.update){const u=el('div','note','Version '+j.update[0]+' is available. ');
    const l=el('a','','Download');l.href=j.update[1];l.target='_blank';l.rel='noopener';u.append(l);$('warn').append(u)}
  if(sessions)$('warn').append(el('div','note',sessions+' claude session'+(sessions>1?'s are':' is')+' running. A running session may keep the account it started with; use Switch & resume to continue your latest chat on the new account.'));
  $('note').replaceChildren();
  if(j.unsaved){const n=el('div','note');n.append(el('div','',j.unsaved+' is logged in but not saved yet. '));
    const b=el('button','pri','Save current login');b.onclick=()=>act('/api/save',0,'Saved.');n.append(b);$('note').append(n)}
  $('list').replaceChildren(...j.accounts.map(a=>{
    const c=el('div','card'+(a.active?' active':'')),g=el('div','grow');
    const dot=el('span','dot');if(a.color)dot.style.background=a.color;c.append(dot);
    const m=el('div','mail',a.email);if(a.label)m.append(el('span','lbl',a.label));g.append(m);
    if(a.expired)g.append(el('div','exp','Login expired. Run claude /login, then Save current login.'));
    g.append(el('div','meta',[a.name,a.org,a.plan].filter(Boolean).join(' · ')));
    if(a.usage){const u=el('div','meta','5h '+pct(a.usage.five_hour)+' · 7d '+pct(a.usage.seven_day)+' · seen '+ago(a.usage.age));
      if(Math.max(a.usage.five_hour||0,a.usage.seven_day||0)>=90)u.className='exp';g.append(u)}
    c.append(g);
    if(a.active)c.append(el('span','badge','ACTIVE'));
    else if(!a.expired){
      const go=(resume)=>{
        if(sessions&&!resume&&!confirm(sessions+' running claude session(s) may keep their current account. Switch anyway?'))return;
        act('/api/switch',{uuid:a.uuid,resume},resume?'Switched to '+a.email+'. Resuming your latest chat in a new window.':'Switched to '+a.email+'. Start a new claude session.')};
      const s=el('button','pri','Switch');s.onclick=()=>go(false);c.append(s);
      const r=el('button','','Switch & resume');r.title='Switch, then continue your latest chat in a new window';r.onclick=()=>go(true);c.append(r)}
    const t=el('button','','Label');t.onclick=()=>editLabel(a,j.colors);c.append(t);
    const d=el('button','','Remove');d.onclick=()=>{if(confirm('Remove '+a.email+' from the switcher? You stay logged in if active.'))act('/api/remove',{uuid:a.uuid},'Removed.')};
    c.append(d);return c}));
  const seen=j.usage_diag&&j.usage_diag.seen,none=!j.accounts.some(a=>a.usage);
  $('hint').textContent=!j.usage_enabled?'Usage alerts are off. Turn them on from the tray menu (installed app).'
    :(none&&seen)?'Usage alerts are on, but Claude Code is not sending usage numbers here (it depends on your plan and Claude Code version). Fields seen: '+seen.keys.join(', ')+'.'
    :(none?'Usage alerts are on. Numbers appear after your next message in a Claude Code session.':'');
  if(!j.accounts.length&&!j.unsaved)$('list').append(el('p','meta','No accounts yet. Run claude /login first.'))}
const pct=v=>v==null?'?':Math.round(v)+'%';
const ago=s=>s>=3600?Math.floor(s/3600)+'h ago':Math.max(1,Math.floor(s/60))+'m ago';
function editLabel(a,colors){
  const l=prompt('Label for '+a.email+' (max 24 chars, empty to clear)',a.label);if(l===null)return;
  const names=['orange','green','blue','purple','yellow','red'];
  const pick=prompt('Color: '+names.map((n,i)=>(i+1)+' '+n).join(', ')+' (empty = none)',a.color?colors.indexOf(a.color)+1:'');if(pick===null)return;
  const color=colors[parseInt(pick,10)-1]||'';
  act('/api/label',{uuid:a.uuid,label:l,color},'Label saved.')}
$('restore').onclick=()=>$('file').click();
$('file').onchange=async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;
  try{const r=await post('/api/restore',JSON.parse(await f.text()));say('Restored '+r.count+' account(s).')}catch(x){say(x.message,1)}load()};
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
            for a in accounts:
                a["usage"] = usage.for_dashboard(a["uuid"])
            return self._send(200, {"accounts": accounts, "unsaved": unsaved, "sessions": switcher.running_sessions(),
                                    "colors": switcher.COLORS, "update": updates.available,
                                    "usage_enabled": usage.enabled(), "usage_diag": usage.diag()})
        if self.path == "/api/backup":
            data = json.dumps(switcher.export_store(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="claude-switcher-backup.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
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
                if body.get("resume"):
                    switcher.resume_latest()
            elif self.path == "/api/remove":
                switcher.remove(body["uuid"])
            elif self.path == "/api/label":
                switcher.set_label(body["uuid"], body.get("label", ""), body.get("color", ""))
            elif self.path == "/api/restore":
                return self._send(200, {"ok": True, "count": switcher.import_store(body)})
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
