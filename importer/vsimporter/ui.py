"""Minimal HTML page at / : submit a URL and list recent jobs. The API key is kept in localStorage."""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VidSwipe Importer</title>
<style>
:root { color-scheme: light dark; --bg:#fff; --fg:#111; --mut:#666; --line:#ddd; --acc:#2563eb; }
@media (prefers-color-scheme: dark) { :root { --bg:#111; --fg:#eee; --mut:#999; --line:#333; --acc:#60a5fa; } }
body { background:var(--bg); color:var(--fg); font:15px/1.4 system-ui,sans-serif; margin:0 auto; max-width:960px; padding:16px; }
h1 { font-size:20px; }
form { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
input { flex:1 1 240px; padding:8px; border:1px solid var(--line); border-radius:6px; background:transparent; color:inherit; }
button { padding:8px 14px; border:0; border-radius:6px; background:var(--acc); color:#fff; cursor:pointer; }
table { width:100%; border-collapse:collapse; font-size:13px; }
td, th { border-bottom:1px solid var(--line); padding:6px 4px; text-align:left; vertical-align:top; word-break:break-word; }
.mut { color:var(--mut); }
.failed { color:#dc2626; } .done { color:#16a34a; } .skipped { color:var(--mut); }
</style>
</head>
<body>
<h1>VidSwipe Importer</h1>
<form id="f">
  <input id="url" type="url" placeholder="Video, playlist or profile URL" required>
  <input id="tags" placeholder="extra tags, comma separated">
  <label class="mut"><input id="expand" type="checkbox" style="flex:none"> expand playlists</label>
  <button>Import</button>
</form>
<p class="mut">API key: <a href="#" id="setkey">set</a> <span id="msg"></span></p>
<table><thead><tr><th>#</th><th>Status</th><th>Title / URL</th><th>Info</th></tr></thead><tbody id="rows"></tbody></table>
<script>
function key() { try { return localStorage.getItem('importerKey') || ''; } catch (e) { return ''; } }
function setKey() { var k = prompt('IMPORTER_API_KEY'); if (k !== null) { try { localStorage.setItem('importerKey', k); } catch (e) {} load(); } }
document.getElementById('setkey').onclick = function (e) { e.preventDefault(); setKey(); };
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
async function api(path, opts) {
  opts = opts || {}; opts.headers = Object.assign({'X-API-Key': key(), 'Content-Type': 'application/json'}, opts.headers || {});
  var r = await fetch(path, opts);
  if (r.status === 401 || r.status === 503) { document.getElementById('msg').textContent = 'Not authorised: set the API key.'; throw new Error('auth'); }
  return r.json();
}
async function load() {
  try {
    var jobs = await api('/api/imports?limit=100');
    document.getElementById('msg').textContent = '';
    document.getElementById('rows').innerHTML = jobs.map(function (j) {
      var info = j.status === 'running' ? j.progress + '%' : (j.error || j.detail || '');
      if (j.watch_name) info += ' (via watch: ' + j.watch_name + ')';
      return '<tr><td>' + j.id + '</td><td class="' + j.status + '">' + j.status + '</td><td>' + esc(j.title || '') +
        '<div class="mut">' + esc(j.url) + '</div></td><td>' + esc(info) + '</td></tr>';
    }).join('');
  } catch (e) {}
}
document.getElementById('f').onsubmit = async function (e) {
  e.preventDefault();
  var tags = document.getElementById('tags').value.split(',').map(function (t) { return t.trim(); }).filter(Boolean);
  try {
    await api('/api/imports', {method: 'POST', body: JSON.stringify({url: document.getElementById('url').value, tags: tags,
      expand_playlists: document.getElementById('expand').checked})});
    document.getElementById('url').value = '';
  } catch (e) {}
  load();
};
load(); setInterval(load, 3000);
</script>
</body>
</html>
"""
