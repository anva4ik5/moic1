const API = {
  token: null,
  base: '',
  async req(method, path, body, isForm) {
    const opts = { method, headers: { 'X-Admin-Token': this.token || '' } };
    if (body !== undefined && !isForm) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(this.base + path, opts);
    if (r.status === 401) { logout(); throw new Error('Unauthorized'); }
    if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${await r.text()}`);
    if (r.status === 204) return null;
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('application/json')) return r.json();
    return r.text();
  },
  get(p) { return this.req('GET', p); },
  post(p, b) { return this.req('POST', p, b); },
  patch(p, b) { return this.req('PATCH', p, b); },
  del(p) { return this.req('DELETE', p); },
};

let SELECTED_AGENT = null;
let CHART_APP = null;
let CHART_DAY = null;
let TIMER = null;

// ----- auth -----

function showLogin() {
  document.getElementById('login').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}
function showApp() {
  document.getElementById('login').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
}
function logout() {
  localStorage.removeItem('admin_token');
  API.token = null;
  if (TIMER) clearInterval(TIMER);
  showLogin();
}

document.getElementById('login-btn').onclick = async () => {
  const t = document.getElementById('token-input').value.trim();
  if (!t) return;
  API.token = t;
  try {
    await API.get('/api/stats/overview');
    localStorage.setItem('admin_token', t);
    showApp();
    init();
  } catch (e) {
    document.getElementById('login-err').textContent = 'Неверный токен';
    API.token = null;
  }
};
document.getElementById('logout-btn').onclick = logout;

// ----- tabs -----

document.querySelectorAll('.tab').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-' + b.dataset.tab).classList.add('active');
    if (b.dataset.tab === 'rules') refreshRules();
    if (b.dataset.tab === 'screenshots') refreshScreens();
  };
});

// ----- agents -----

function fmtSec(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
function fmtTs(s) {
  return new Date(s).toLocaleString();
}

async function refreshOverview() {
  try {
    const o = await API.get('/api/stats/overview');
    document.getElementById('overview').textContent =
      `Агенты: ${o.agents_online}/${o.agents_total} online · события 24ч: ${o.events_24h} · скрины 24ч: ${o.screenshots_24h}`;
  } catch (_) {}
}

async function refreshAgents() {
  const agents = await API.get('/api/agents');
  const tbody = document.querySelector('#agents-table tbody');
  tbody.innerHTML = '';
  agents.forEach(a => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${a.id}</td>
      <td>${escapeHtml(a.hostname)}</td>
      <td>${escapeHtml(a.username)}</td>
      <td>${escapeHtml(a.os_info)}</td>
      <td><span class="dot ${a.online ? 'on' : ''}"></span>${a.online ? 'online' : 'offline'}</td>
      <td>${a.consented ? '<span class="tag ok">да</span>' : '<span class="tag warn">нет</span>'}</td>
      <td>${a.screenshot_interval ? a.screenshot_interval + ' сек' : '—'}</td>
      <td>${a.tracking_enabled ? '✓' : '✗'}</td>
      <td>${a.block_enabled ? '✓' : '✗'}</td>`;
    tr.onclick = () => selectAgent(a);
    if (SELECTED_AGENT && SELECTED_AGENT.id === a.id) tr.style.background = '#21262d';
    tbody.appendChild(tr);
  });

  // populate selectors elsewhere
  const ssSel = document.getElementById('ss-agent');
  const ruleSel = document.getElementById('rule-agent');
  [ssSel, ruleSel].forEach(s => {
    const cur = s.value;
    s.innerHTML = '<option value="">' + (s === ruleSel ? 'Все агенты' : 'Все агенты') + '</option>';
    agents.forEach(a => {
      const o = document.createElement('option');
      o.value = a.id;
      o.textContent = `#${a.id} ${a.hostname} (${a.username})`;
      s.appendChild(o);
    });
    s.value = cur;
  });
}

async function selectAgent(a) {
  SELECTED_AGENT = a;
  document.getElementById('agent-detail-title').textContent =
    `Агент #${a.id} — ${a.hostname} / ${a.username}`;
  document.getElementById('agent-detail').classList.remove('hidden');
  document.getElementById('d-interval').value = a.screenshot_interval;
  document.getElementById('d-tracking').checked = a.tracking_enabled;
  document.getElementById('d-block').checked = a.block_enabled;
  await Promise.all([refreshAgents(), refreshAgentStats(a.id), refreshAgentEvents(a.id)]);
}

document.getElementById('d-save').onclick = async () => {
  if (!SELECTED_AGENT) return;
  const body = {
    screenshot_interval: parseInt(document.getElementById('d-interval').value || '0', 10),
    tracking_enabled: document.getElementById('d-tracking').checked,
    block_enabled: document.getElementById('d-block').checked,
  };
  await API.patch(`/api/agents/${SELECTED_AGENT.id}`, body);
  await refreshAgents();
};

document.getElementById('d-delete').onclick = async () => {
  if (!SELECTED_AGENT) return;
  if (!confirm('Удалить агента и все его данные?')) return;
  await API.del(`/api/agents/${SELECTED_AGENT.id}`);
  SELECTED_AGENT = null;
  document.getElementById('agent-detail').classList.add('hidden');
  document.getElementById('agent-detail-title').textContent = 'Выберите агента';
  await refreshAgents();
};

document.querySelectorAll('#agent-detail [data-cmd]').forEach(b => {
  b.onclick = async () => {
    if (!SELECTED_AGENT) return;
    await API.post(`/api/commands/agents/${SELECTED_AGENT.id}`, { type: b.dataset.cmd, payload: {} });
    flash(b);
  };
});
document.getElementById('d-send-msg').onclick = async () => {
  if (!SELECTED_AGENT) return;
  const text = document.getElementById('msg-text').value;
  if (!text) return;
  await API.post(`/api/commands/agents/${SELECTED_AGENT.id}`, { type: 'message', payload: { text } });
  document.getElementById('msg-text').value = '';
};
document.getElementById('d-kill').onclick = async () => {
  if (!SELECTED_AGENT) return;
  const proc = document.getElementById('kill-proc').value.trim();
  if (!proc) return;
  await API.post(`/api/commands/agents/${SELECTED_AGENT.id}`, { type: 'kill', payload: { process: proc } });
  document.getElementById('kill-proc').value = '';
};

async function refreshAgentStats(agentId) {
  const s = await API.get(`/api/stats/agents/${agentId}?days=7`);
  const labels = s.by_app.map(x => x.app);
  const data = s.by_app.map(x => Math.round(x.seconds / 60));
  if (CHART_APP) CHART_APP.destroy();
  CHART_APP = new Chart(document.getElementById('chart-app'), {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Минуты', data, backgroundColor: '#3fb950' }] },
    options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#8b949e' } }, y: { ticks: { color: '#8b949e' } } } },
  });
  const dlabels = s.by_day.map(x => x.date);
  const ddata = s.by_day.map(x => Math.round(x.seconds / 60));
  if (CHART_DAY) CHART_DAY.destroy();
  CHART_DAY = new Chart(document.getElementById('chart-day'), {
    type: 'line',
    data: { labels: dlabels, datasets: [{ label: 'Минуты/день', data: ddata, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.2)', fill: true, tension: 0.25 }] },
    options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#8b949e' } }, y: { ticks: { color: '#8b949e' } } } },
  });
}

async function refreshAgentEvents(agentId) {
  const ev = await API.get(`/api/events?agent_id=${agentId}&since_minutes=1440&limit=100`);
  const tbody = document.querySelector('#events-table tbody');
  tbody.innerHTML = '';
  ev.forEach(e => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${fmtTs(e.ts)}</td><td>${e.type}</td><td>${escapeHtml(e.app)}</td><td>${escapeHtml(e.title)}</td><td>${e.duration}</td>`;
    tbody.appendChild(tr);
  });
}

// ----- rules -----

document.getElementById('rule-add').onclick = async () => {
  const proc = document.getElementById('rule-process').value.trim();
  if (!proc) return;
  const reason = document.getElementById('rule-reason').value.trim();
  const aid = document.getElementById('rule-agent').value;
  await API.post('/api/rules', {
    process: proc,
    reason,
    enabled: true,
    daily_limit_seconds: 0,
    agent_id: aid ? parseInt(aid, 10) : null,
  });
  document.getElementById('rule-process').value = '';
  document.getElementById('rule-reason').value = '';
  refreshRules();
};

async function refreshRules() {
  const rules = await API.get('/api/rules');
  const agents = await API.get('/api/agents');
  const byId = Object.fromEntries(agents.map(a => [a.id, a]));
  const tbody = document.querySelector('#rules-table tbody');
  tbody.innerHTML = '';
  rules.forEach(r => {
    const tr = document.createElement('tr');
    const target = r.agent_id ? `#${r.agent_id} ${byId[r.agent_id]?.hostname || ''}` : 'все';
    tr.innerHTML = `<td>${r.id}</td><td>${escapeHtml(r.process)}</td><td>${target}</td><td>${escapeHtml(r.reason)}</td><td>${r.enabled ? '✓' : '✗'}</td><td><button class="danger" data-id="${r.id}">×</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll('button[data-id]').forEach(b => {
    b.onclick = async (e) => {
      e.stopPropagation();
      await API.del('/api/rules/' + b.dataset.id);
      refreshRules();
    };
  });
}

// ----- screenshots -----

document.getElementById('ss-refresh').onclick = refreshScreens;

// <img src> cannot send custom headers, so fetch each image as a blob with the
// admin token and use object URLs for display.
async function refreshScreens() {
  const aid = document.getElementById('ss-agent').value;
  const range = document.getElementById('ss-range').value;
  const q = new URLSearchParams({ since_minutes: range, limit: '200' });
  if (aid) q.set('agent_id', aid);
  const list = await API.get('/api/screenshots?' + q.toString());
  const grid = document.getElementById('ss-grid');
  grid.innerHTML = '';
  for (const s of list) {
    const card = document.createElement('div');
    card.className = 'ss-card';
    card.innerHTML = `<img alt="screenshot" /><div class="meta">#${s.agent_id} · ${fmtTs(s.ts)} · ${(s.size_bytes/1024).toFixed(0)} KB</div>`;
    grid.appendChild(card);
    fetch(s.url, { headers: { 'X-Admin-Token': API.token } })
      .then(r => r.blob())
      .then(b => {
        const u = URL.createObjectURL(b);
        const img = card.querySelector('img');
        img.src = u;
        img.onclick = () => openModal(u);
      })
      .catch(() => { /* ignore single image error */ });
  }
}

function openModal(url) {
  const m = document.getElementById('modal');
  document.getElementById('modal-img').src = url;
  m.classList.remove('hidden');
}
document.getElementById('modal').onclick = () => document.getElementById('modal').classList.add('hidden');

// ----- helpers -----

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}
function flash(el) {
  const o = el.style.background;
  el.style.background = '#3fb950';
  setTimeout(() => { el.style.background = o; }, 350);
}

async function init() {
  await refreshOverview();
  await refreshAgents();
  if (TIMER) clearInterval(TIMER);
  TIMER = setInterval(async () => {
    await refreshOverview();
    await refreshAgents();
    if (SELECTED_AGENT) await refreshAgentEvents(SELECTED_AGENT.id);
  }, 5000);
}

(function boot() {
  const saved = localStorage.getItem('admin_token');
  if (saved) {
    API.token = saved;
    showApp();
    init().catch(() => logout());
  } else {
    showLogin();
  }
})();
