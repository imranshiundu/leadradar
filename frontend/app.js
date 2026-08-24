const API = localStorage.getItem('api_url') || (window.location.origin + '/lr');
const TOKEN = localStorage.getItem('dashboard_token') || '';

async function api(path, opts = {}) {
    const headers = { ...opts.headers };
    if (TOKEN) headers['X-Dashboard-Token'] = TOKEN;
    if (!opts.body || typeof opts.body === 'string') headers['Content-Type'] = 'application/json';
    const r = await fetch(`${API}${path}`, { ...opts, headers });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
}

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

function apiForm(path, fd) {
    const h = {};
    if (TOKEN) h['X-Dashboard-Token'] = TOKEN;
    return fetch(`${API}${path}`, { method: 'POST', headers: h, body: fd }).then(r => r.json());
}

function toast(msg, type = 'success') {
    const c = document.getElementById('toasts');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function badge(text) { return text ? `<span class="badge badge-${text}">${text}</span>` : ''; }

// ── Router ──────────────────────────────────────────────
const routes = { '/': dash, '/campaigns': campaigns, '/pipeline': pipeline, '/replies': replies, '/import': importer, '/analytics': analytics, '/settings': settings };

function go(hash) {
    const p = (hash || '#/').replace('#', '');
    (routes[p] || notFound)();
}

function nav() {
    const counts = {};
    const items = [
        ['/', '📊', 'Dashboard'],
        ['/campaigns', '📨', 'Campaigns'],
        ['/pipeline', '🔀', 'Pipeline'],
        ['/replies', '💬', 'Replies'],
        ['/analytics', '📈', 'Analytics'],
    ];
    document.getElementById('nav').innerHTML =
        items.map(([h, icon, label]) =>
            `<a href="#${h}" class="nav-item" data-route="${h}"><span class="icon">${icon}</span>${label}</a>`
        ).join('') +
        `<a href="#/import" class="nav-item" data-route="/import"><span class="icon">📥</span>Import</a>` +
        `<a href="#/settings" class="nav-item" data-route="/settings"><span class="icon">⚙</span>Settings</a>`;
    highlight();
}

function highlight() {
    const p = location.hash.replace('#', '') || '/';
    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.route === p);
    });
}

async function checkServer() {
    try {
        const r = await api('/health');
        const dot = document.querySelector('.status-dot');
        const txt = document.querySelector('.status-text');
        dot.className = 'status-dot ok';
        txt.textContent = 'Connected';
    } catch {
        const dot = document.querySelector('.status-dot');
        const txt = document.querySelector('.status-text');
        if (dot) { dot.className = 'status-dot err'; txt.textContent = 'Offline'; }
    }
}

// ── Dashboard ──────────────────────────────────────────
async function dash() {
    main('<div class="loading">Loading leads...</div>');
    try {
        const { leads } = await api('/api/leads?limit=200');
        const s = { total: leads.length, sent: 0, approved: 0, replied: 0, high: 0 };
        leads.forEach(l => {
            if (l.status === 'sent') s.sent++;
            if (l.status === 'approved') s.approved++;
            if (l.pipeline_stage === 'replied') s.replied++;
            if (l.priority === 'high') s.high++;
        });
        main(`
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">${s.total}</div><div class="stat-label">Total Leads</div></div>
                <div class="stat-card"><div class="stat-value green">${s.sent}</div><div class="stat-label">Sent</div></div>
                <div class="stat-card"><div class="stat-value blue">${s.approved}</div><div class="stat-label">Approved</div></div>
                <div class="stat-card"><div class="stat-value purple">${s.replied}</div><div class="stat-label">Replied</div></div>
                <div class="stat-card"><div class="stat-value red">${s.high}</div><div class="stat-label">High Priority</div></div>
            </div>
            <div class="card-header"><div class="card-title">Recent Leads</div><span class="card-subtitle">${s.total} total</span></div>
            ${leads.slice(0, 50).map(l => `
            <div class="card" style="padding:1rem">
                <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem">
                    <a href="#/lead/${l.id}" style="font-weight:700;font-size:.9rem">${esc(l.name)}</a>
                    ${badge(l.status)}${l.priority === 'high' ? badge('high') : ''}
                </div>
                <div class="meta-grid">
                    ${l.email ? `<div class="meta-item"><span class="meta-label">Email</span><span class="meta-value mono">${esc(l.email)}</span></div>` : ''}
                    ${l.phone ? `<div class="meta-item"><span class="meta-label">Phone</span><span class="meta-value mono">${esc(l.phone)}</span></div>` : ''}
                    ${l.event_name ? `<div class="meta-item"><span class="meta-label">Event</span><span class="meta-value" style="color:var(--accent2)">${esc(l.event_name)}</span></div>` : ''}
                    ${l.pipeline_stage ? `<div class="meta-item"><span class="meta-label">Stage</span><span class="meta-value">${l.pipeline_stage}</span></div>` : ''}
                </div>
            </div>`).join('')}
            ${s.total === 0 ? empty('No leads yet', 'Import contacts to get started', '#/import') : ''}
        `);
    } catch (e) { main(`<div class="empty"><div class="empty-icon">⚠</div><div class="empty-title">Connection failed</div><div class="empty-desc">${e.message}. Go to <a href="#/settings" style="color:var(--accent2)">Settings</a> to configure the backend URL.</div></div>`); }
}

// ── Campaigns ──────────────────────────────────────────
async function campaigns() {
    main('<div class="loading">Loading campaigns...</div>');
    try {
        const { campaigns: list } = await api('/api/campaigns');
        main(`
            <div class="card-header"><div class="card-title">Campaigns</div><a href="#/import" class="btn btn-accent btn-sm">+ New</a></div>
            ${list.map(c => {
                const a = c.analytics || {};
                const pct = a.targets ? Math.round((a.finished / a.targets) * 100) : 0;
                const interested = (a.reply_breakdown || {}).interested || 0;
                return `
            <div class="card">
                <div class="card-header">
                    <div><div class="card-title">${esc(c.name)}</div><div class="card-subtitle">${c.subject_template || ''}</div></div>
                    ${badge(c.status)}
                </div>
                <div class="stats-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:.75rem">
                    <div class="stat-card" style="padding:.6rem .8rem"><div class="stat-value" style="font-size:1.2rem">${a.targets || 0}</div><div class="stat-label" style="font-size:.6rem">Targets</div></div>
                    <div class="stat-card" style="padding:.6rem .8rem"><div class="stat-value blue" style="font-size:1.2rem">${a.messages_sent || 0}</div><div class="stat-label" style="font-size:.6rem">Sent</div></div>
                    <div class="stat-card" style="padding:.6rem .8rem"><div class="stat-value green" style="font-size:1.2rem">${a.stopped_replied || 0}</div><div class="stat-label" style="font-size:.6rem">Replies</div></div>
                    <div class="stat-card" style="padding:.6rem .8rem"><div class="stat-value purple" style="font-size:1.2rem">${interested}</div><div class="stat-label" style="font-size:.6rem">Interested</div></div>
                </div>
                <div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:.5rem">
                    <span class="dimmest" style="font-size:.72rem">${a.finished || 0}/${a.targets || 0} reached (${pct}%)</span>
                    <div style="display:flex;gap:.3rem">
                        ${c.status !== 'active' ? `<button class="btn btn-accent btn-xs" onclick="campAct(${c.id},'active')">Activate</button>` : `<button class="btn btn-outline btn-xs" onclick="campAct(${c.id},'paused')">Pause</button><button class="btn btn-accent btn-xs" onclick="campRun(${c.id})">Run</button>`}
                        <button class="btn btn-ghost btn-xs" onclick="campAttach(${c.id})">Attach</button>
                    </div>
                </div>
            </div>`;}).join('')}
            ${list.length === 0 ? empty('No campaigns yet', 'Create your first campaign', '#/import') : ''}
        `);
    } catch (e) { main(`<div class="empty"><div class="empty-icon">⚠</div><div class="empty-title">${e.message}</div></div>`); }
}

window.campAct = async (id, status) => { await post(`/api/campaigns/${id}/status`, { status }); toast(`Campaign ${status}`); campaigns(); };
window.campRun = async (id) => { const r = await post(`/api/campaigns/${id}/run-once`, {}); toast(`Sent ${r.sent} emails`); campaigns(); };
window.campAttach = async (id) => { const r = await post(`/api/campaigns/${id}/attach`, {}); toast(`Attached ${r.attached} contacts`); };

// ── Pipeline ───────────────────────────────────────────
async function pipeline() {
    main('<div class="loading">Loading pipeline...</div>');
    try {
        const { leads } = await api('/api/leads?limit=1000');
        const stages = ['new', 'contacted', 'replied', 'meeting', 'won', 'lost'];
        const cols = {};
        stages.forEach(s => cols[s] = []);
        leads.forEach(l => (cols[l.pipeline_stage || 'new'] || cols.new).push(l));
        main(`
            <div class="card-header"><div class="card-title">Pipeline</div><span class="card-subtitle">${leads.length} contacts</span></div>
            <div class="board">
                ${stages.map(s => `
                <div class="column">
                    <div class="column-header"><span class="column-title">${s}</span><span class="column-count">${cols[s].length}</span></div>
                    ${cols[s].slice(0, 25).map(l => `
                    <div class="lead-card">
                        <div class="lead-card-name">${esc(l.name)}</div>
                        ${l.email ? `<div class="lead-card-email">${esc(l.email)}</div>` : ''}
                        ${l.event_name ? `<div class="lead-card-event">📅 ${esc(l.event_name)}</div>` : ''}
                        ${l.priority === 'high' ? '<span class="badge badge-high" style="margin-top:.3rem">high</span>' : ''}
                        <div class="stage-select">
                            <select onchange="moveStage(${l.id},this.value)">
                                ${stages.map(st => `<option value="${st}" ${st === s ? 'selected' : ''}>${st}</option>`).join('')}
                            </select>
                        </div>
                    </div>`).join('')}
                </div>`).join('')}
            </div>
        `);
    } catch (e) { main(`<div class="empty"><div class="empty-icon">⚠</div><div class="empty-title">${e.message}</div></div>`); }
}

window.moveStage = async (id, stage) => { const fd = new FormData(); fd.append('stage', stage); await apiForm(`/api/leads/${id}/stage`, fd); };

// ── Replies ────────────────────────────────────────────
async function replies() {
    main('<div class="loading">Loading replies...</div>');
    try {
        const { replies: list } = await api('/api/replies');
        main(`
            <div class="card-header"><div class="card-title">Replies</div><button class="btn btn-accent btn-sm" onclick="pollNow()">Poll inbox</button></div>
            ${list.length > 0 ? `
            <div class="card" style="padding:0;overflow:hidden">
                <table><thead><tr><th>Contact</th><th>Email</th><th>Keyword</th><th>Direction</th><th>Date</th></tr></thead>
                <tbody>${list.map(r => `<tr>
                    <td><strong>${esc(r.lead_name || 'Unknown')}</strong></td>
                    <td class="mono">${esc(r.from_email || r.to_email || '')}</td>
                    <td>${badge(r.keyword || 'unknown')}</td>
                    <td>${r.direction}</td>
                    <td class="dim" style="font-size:.75rem">${r.received_at || r.sent_at || ''}</td>
                </tr>`).join('')}</tbody></table>
            </div>` : empty('No replies yet', 'Poll your inbox to detect new replies')}
        `);
    } catch (e) { main(`<div class="empty"><div class="empty-icon">⚠</div><div class="empty-title">${e.message}</div></div>`); }
}

window.pollNow = async () => { const r = await post('/api/inbox/poll', {}); toast(`Polled: ${r.matched || 0} replies`); replies(); };

// ── Import ─────────────────────────────────────────────
function importer() {
    main(`
        <div class="card-header"><div class="card-title">Import Contacts</div></div>
        <div class="card">
            <p class="muted" style="font-size:.82rem;margin-bottom:1rem">Paste or upload a contact list. Supports TSV, CSV, pipe tables, and plain lines.</p>
            <form id="importForm">
                <label>Upload file</label>
                <input type="file" name="file" accept=".txt,.csv,.md,.tsv">
                <label>...or paste text</label>
                <textarea name="text" rows="8" placeholder="1&#9;Bigmiitch Events&#9;+254 724 214 461&#9;info@example.com&#9;🔥🔥🔥"></textarea>
                <div class="form-actions"><button type="submit" class="btn btn-accent">Import contacts</button></div>
            </form>
        </div>
        <div class="card">
            <div class="card-header"><div class="card-title">Create Campaign</div></div>
            <form id="campaignForm">
                <div class="form-row">
                    <div><label>Name</label><input type="text" name="name" required placeholder="e.g. Taptap Oct/Nov"></div>
                    <div><label>Subject</label><input type="text" name="subject_template" required placeholder="Taptap for {{name}}"></div>
                </div>
                <label>Body template</label>
                <textarea name="body_template" rows="6" required placeholder="Hi {{first_name}},..."></textarea>
                <div class="form-row">
                    <div><label>Follow-up 1 (days)</label><input type="number" name="follow_up_1_days" value="3"></div>
                    <div><label>Follow-up 2 (days)</label><input type="number" name="follow_up_2_days" value="7"></div>
                </div>
                <label>Follow-up 1 body</label>
                <textarea name="follow_up_1_body" rows="3" placeholder="Follow-up body..."></textarea>
                <label>Follow-up 2 body</label>
                <textarea name="follow_up_2_body" rows="3" placeholder="Second follow-up..."></textarea>
                <div class="form-actions"><button type="submit" class="btn btn-accent">Create campaign</button></div>
            </form>
        </div>
        <div class="card">
            <div class="card-header"><div class="card-title">Verify Email</div></div>
            <form id="verifyForm" style="display:flex;gap:.5rem;align-items:flex-end">
                <div style="flex:1"><label>Email</label><input type="email" name="email" required placeholder="test@gmail.com"></div>
                <button type="submit" class="btn btn-accent btn-sm" style="margin-top:1.1rem">Verify</button>
            </form>
            <div id="verifyResult"></div>
        </div>
    `);
    document.getElementById('importForm').onsubmit = handleImport;
    document.getElementById('campaignForm').onsubmit = handleCampaign;
    document.getElementById('verifyForm').onsubmit = handleVerify;
}

async function handleImport(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await apiForm('/api/import', fd);
    r.ok ? toast(`Imported ${r.imported} contacts (${r.duplicates} dupes)`) : toast(r.detail || 'Import failed', 'error');
}

async function handleCampaign(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await apiForm('/api/campaigns/create', fd);
    (r.ok || r.campaign_id) ? toast('Campaign created') : toast(r.detail || 'Failed', 'error');
}

async function handleVerify(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await post('/api/verify-email', { email: fd.get('email') });
    document.getElementById('verifyResult').innerHTML = `
        <div style="margin-top:1rem;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.5rem">
            <div class="stat-card" style="padding:.5rem .7rem;text-align:center"><div style="font-size:.65rem;color:var(--text4);text-transform:uppercase">Status</div>${badge(r.status)}</div>
            <div class="stat-card" style="padding:.5rem .7rem;text-align:center"><div style="font-size:.65rem;color:var(--text4);text-transform:uppercase">MX</div><div style="font-size:.85rem">${r.mx_valid ? '✅' : '❌'}</div></div>
            <div class="stat-card" style="padding:.5rem .7rem;text-align:center"><div style="font-size:.65rem;color:var(--text4);text-transform:uppercase">Disposable</div><div style="font-size:.85rem">${r.disposable ? '⚠ Yes' : '✅ No'}</div></div>
            <div class="stat-card" style="padding:.5rem .7rem;text-align:center"><div style="font-size:.65rem;color:var(--text4);text-transform:uppercase">Free</div><div style="font-size:.85rem">${r.free_provider ? '📧' : '🏢'}</div></div>
            <div class="stat-card" style="padding:.5rem .7rem;text-align:center"><div style="font-size:.65rem;color:var(--text4);text-transform:uppercase">Role</div><div style="font-size:.85rem">${r.role_account ? '⚠' : '✅'}</div></div>
        </div>
    `;
}

// ── Analytics ──────────────────────────────────────────
async function analytics() {
    main('<div class="loading">Loading analytics...</div>');
    try {
        const d = await api('/api/analytics');
        main(`
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-value">${d.total_leads || 0}</div><div class="stat-label">Leads</div></div>
                <div class="stat-card"><div class="stat-value blue">${d.total_sent || 0}</div><div class="stat-label">Emails Sent</div></div>
                <div class="stat-card"><div class="stat-value green">${d.total_replies || 0}</div><div class="stat-label">Replies</div></div>
                <div class="stat-card"><div class="stat-value purple">${d.total_interested || 0}</div><div class="stat-label">Interested</div></div>
            </div>
            <div class="card-header"><div class="card-title">Daily Sends (14 days)</div></div>
            <div class="card">
                ${(d.daily_sends || []).map(ds => `
                <div style="display:flex;justify-content:space-between;padding:.4rem 0;border-bottom:1px solid var(--border)">
                    <span class="mono" style="font-size:.78rem">${ds.day}</span><span style="font-weight:600;font-size:.82rem">${ds.sent}</span>
                </div>`).join('')}
                ${!d.daily_sends?.length ? '<p class="muted" style="text-align:center;padding:2rem;font-size:.82rem">No sends recorded yet.</p>' : ''}
            </div>
            <div class="card-header"><div class="card-title">Campaign Breakdown</div></div>
            ${(d.campaigns || []).map(c => `
            <div class="card">
                <div class="card-header">
                    <div class="card-title">${esc(c.name || 'Campaign #' + c.campaign_id)}</div>
                    <span class="dim" style="font-size:.75rem">Sent: ${c.messages_sent || 0} | Replies: ${c.stopped_replied || 0}</span>
                </div>
                ${c.reply_breakdown ? `<div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem">${Object.entries(c.reply_breakdown).map(([k, v]) => badge(`${k}: ${v}`)).join('')}</div>` : ''}
            </div>`).join('')}
        `);
    } catch (e) { main(`<div class="empty"><div class="empty-icon">⚠</div><div class="empty-title">${e.message}</div></div>`); }
}

// ── Settings ───────────────────────────────────────────
function settings() {
    main(`
        <div class="card-header"><div class="card-title">Settings</div></div>
        <div class="card">
            <form id="settingsForm">
                <label>Backend API URL</label>
                <input type="url" id="apiUrl" value="${API}" placeholder="http://169.58.128.213/lr">
                <label>Dashboard Token (optional)</label>
                <input type="password" id="dashToken" value="${TOKEN}" placeholder="Leave blank if no auth">
                <div class="form-actions">
                    <button type="submit" class="btn btn-accent">Save</button>
                    <button type="button" class="btn btn-outline" onclick="testConn()">Test connection</button>
                </div>
            </form>
            <div id="connTest"></div>
        </div>
    `);
    document.getElementById('settingsForm').onsubmit = e => {
        e.preventDefault();
        localStorage.setItem('api_url', document.getElementById('apiUrl').value);
        localStorage.setItem('dashboard_token', document.getElementById('dashToken').value);
        toast('Settings saved — reloading');
        setTimeout(() => location.reload(), 500);
    };
}

window.testConn = async () => {
    try {
        const r = await api('/health');
        document.getElementById('connTest').innerHTML = `<p style="color:var(--green);margin-top:1rem;font-size:.82rem">✅ Connected. DB: ${r.db}</p>`;
    } catch (e) {
        document.getElementById('connTest').innerHTML = `<p style="color:var(--red);margin-top:1rem;font-size:.82rem">❌ ${e.message}</p>`;
    }
};

// ── Utils ──────────────────────────────────────────────
function main(html) { document.getElementById('main').innerHTML = html; }
function empty(title, desc, link) {
    return `<div class="empty"><div class="empty-icon">📭</div><div class="empty-title">${title}</div><div class="empty-desc">${desc}${link ? ` <a href="${link}" style="color:var(--accent2)">→ Get started</a>` : ''}</div></div>`;
}
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function notFound() { main(empty('Page not found', '')); }
function handleSearch(e) { /* TODO: client-side filter */ }

// ── Init ───────────────────────────────────────────────
nav();
checkServer();
window.addEventListener('hashchange', () => { go(location.hash); highlight(); });
go(location.hash || '#/');
