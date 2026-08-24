// LeadRadarSafe Pro — Frontend SPA
const API = localStorage.getItem('api_url') || 'http://169.58.128.213/lr';
const TOKEN = localStorage.getItem('dashboard_token') || '';

function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...opts.headers };
    if (TOKEN) headers['X-Dashboard-Token'] = TOKEN;
    return fetch(`${API}${path}`, { ...opts, headers }).then(r => r.json());
}

function apiPost(path, body) {
    return api(path, { method: 'POST', body: JSON.stringify(body) });
}

function apiForm(path, formData) {
    const headers = {};
    if (TOKEN) headers['X-Dashboard-Token'] = TOKEN;
    return fetch(`${API}${path}`, { method: 'POST', headers, body: formData }).then(r => r.json());
}

function toast(msg, type = 'success') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function badge(text) { return `<span class="badge badge-${text}">${text}</span>`; }

// ── Router ────────────────────────────────────────────────────

const routes = {
    '/': renderDashboard,
    '/campaigns': renderCampaigns,
    '/pipeline': renderPipeline,
    '/replies': renderReplies,
    '/import': renderImport,
    '/analytics': renderAnalytics,
    '/settings': renderSettings,
};

function navigate(hash) {
    const path = hash.replace('#', '') || '/';
    const render = routes[path] || renderNotFound;
    document.getElementById('main').innerHTML = '<p class="dim" style="text-align:center;padding:4rem">Loading...</p>';
    render();
}

function renderNav() {
    const links = [
        ['/', 'Leads'], ['/campaigns', 'Campaigns'], ['/pipeline', 'Pipeline'],
        ['/replies', 'Replies'], ['/analytics', 'Analytics'],
    ];
    document.getElementById('nav').innerHTML =
        links.map(([h, t]) => `<a href="#${h}" class="btn btn-outline">${t}</a>`).join('') +
        '<a href="#/import" class="btn btn-primary">+ Import</a>' +
        '<a href="#/settings" class="btn btn-outline btn-xs">Settings</a>';
}

// ── Pages ─────────────────────────────────────────────────────

async function renderDashboard() {
    const { leads } = await api('/api/leads?limit=200');
    const sent = leads.filter(l => l.status === 'sent').length;
    const approved = leads.filter(l => l.status === 'approved').length;
    const replied = leads.filter(l => l.pipeline_stage === 'replied').length;
    document.getElementById('main').innerHTML = `
        <div class="stats">
            <div class="stat"><span class="stat-value">${leads.length}</span><span class="stat-label">Total Leads</span></div>
            <div class="stat"><span class="stat-value green">${sent}</span><span class="stat-label">Sent</span></div>
            <div class="stat"><span class="stat-value blue">${approved}</span><span class="stat-label">Approved</span></div>
            <div class="stat"><span class="stat-value purple">${replied}</span><span class="stat-label">Replied</span></div>
        </div>
        <h2 style="font-size:1rem;font-weight:700;margin-bottom:1rem">Recent Leads</h2>
        ${leads.slice(0, 50).map(l => `
        <div class="card">
            <div class="card-title">
                <a href="#/lead/${l.id}">${l.name}</a>
                ${badge(l.status)}
                ${l.priority === 'high' ? badge('high') : ''}
            </div>
            <div class="meta-grid">
                ${l.email ? `<div class="meta-item"><span class="meta-label">Email</span><span class="meta-value mono">${l.email}</span></div>` : ''}
                ${l.phone ? `<div class="meta-item"><span class="meta-label">Phone</span><span class="meta-value mono">${l.phone}</span></div>` : ''}
                ${l.event_name ? `<div class="meta-item"><span class="meta-label">Event</span><span class="meta-value" style="color:var(--accent-light)">${l.event_name}</span></div>` : ''}
                ${l.pipeline_stage ? `<div class="meta-item"><span class="meta-label">Stage</span><span class="meta-value">${l.pipeline_stage}</span></div>` : ''}
            </div>
        </div>`).join('')}
        ${leads.length === 0 ? '<div class="card" style="text-align:center;padding:4rem"><p class="muted">No leads yet. Import contacts first.</p></div>' : ''}
    `;
}

async function renderCampaigns() {
    const { campaigns } = await api('/api/campaigns');
    document.getElementById('main').innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem">
            <h1 style="font-size:1.3rem;font-weight:800">Campaigns</h1>
            <a href="#/import" class="btn btn-primary">+ New campaign</a>
        </div>
        ${campaigns.map(c => {
            const a = c.analytics || {};
            const pct = a.targets ? Math.round((a.finished / a.targets) * 100) : 0;
            return `
        <div class="card">
            <div class="card-title">
                <strong>${c.name}</strong>
                ${badge(c.status)}
            </div>
            <div class="stats" style="margin:.75rem 0">
                <div class="stat" style="padding:.6rem 1rem"><span class="stat-value" style="font-size:1.3rem">${a.targets || 0}</span><span class="stat-label">Targets</span></div>
                <div class="stat" style="padding:.6rem 1rem"><span class="stat-value blue" style="font-size:1.3rem">${a.messages_sent || 0}</span><span class="stat-label">Sent</span></div>
                <div class="stat" style="padding:.6rem 1rem"><span class="stat-value green" style="font-size:1.3rem">${a.stopped_replied || 0}</span><span class="stat-label">Replies</span></div>
                <div class="stat" style="padding:.6rem 1rem"><span class="stat-value purple" style="font-size:1.3rem">${(a.reply_breakdown || {}).interested || 0}</span><span class="stat-label">Interested</span></div>
            </div>
            <div class="campaign-progress"><div class="bar" style="width:${pct}%"></div></div>
            <p class="dim" style="font-size:.72rem;margin-top:.4rem">${a.finished || 0}/${a.targets || 0} contacts reached (${pct}%)</p>
            <div class="actions">
                ${c.status !== 'active' ?
                    `<button class="btn btn-primary btn-sm" onclick="activateCampaign(${c.id})">Activate</button>` :
                    `<button class="btn btn-outline btn-sm" onclick="pauseCampaign(${c.id})">Pause</button>
                     <button class="btn btn-primary btn-sm" onclick="runCampaign(${c.id})">Run now</button>`
                }
                <button class="btn btn-outline btn-sm" onclick="attachContacts(${c.id})">Attach contacts</button>
            </div>
        </div>`;}).join('')}
        ${campaigns.length === 0 ? '<div class="card" style="text-align:center;padding:4rem"><p class="muted">No campaigns yet.</p></div>' : ''}
    `;
}

async function renderPipeline() {
    const { leads } = await api('/api/leads?limit=1000');
    const stages = { new: [], contacted: [], replied: [], meeting: [], won: [], lost: [] };
    leads.forEach(l => { (stages[l.pipeline_stage || 'new'] || stages.new).push(l); });
    document.getElementById('main').innerHTML = `
        <h1 style="font-size:1.3rem;font-weight:800;margin-bottom:1.5rem">Pipeline</h1>
        <div class="board">
            ${Object.entries(stages).map(([stage, items]) => `
            <div class="column">
                <h3>${stage} <span class="count">${items.length}</span></h3>
                ${items.slice(0, 30).map(l => `
                <div class="card">
                    <strong style="font-size:.88rem">${l.name}</strong>
                    ${l.email ? `<div class="mono muted" style="font-size:.72rem;margin-top:.2rem">${l.email}</div>` : ''}
                    ${l.event_name ? `<div class="event" style="font-size:.72rem">📅 ${l.event_name}</div>` : ''}
                    ${l.priority === 'high' ? '<span class="badge badge-high" style="margin-top:.3rem">high</span>' : ''}
                    <div class="stage-form">
                        <select onchange="moveStage(${l.id}, this.value)">
                            ${Object.keys(stages).map(s => `<option value="${s}" ${s === stage ? 'selected' : ''}>${s}</option>`).join('')}
                        </select>
                    </div>
                </div>`).join('')}
            </div>`).join('')}
        </div>
    `;
}

async function renderReplies() {
    const { replies } = await api('/api/replies').catch(() => ({ replies: [] }));
    document.getElementById('main').innerHTML = `
        <h1 style="font-size:1.3rem;font-weight:800;margin-bottom:1.5rem">Replies</h1>
        ${replies.length > 0 ? `
            <table>
                <tr><th>Contact</th><th>Email</th><th>Keyword</th><th>Direction</th><th>Date</th></tr>
                ${replies.map(r => `
                <tr>
                    <td>${r.lead_name || 'Unknown'}</td>
                    <td class="mono">${r.from_email || r.to_email || ''}</td>
                    <td>${badge(r.keyword || 'unknown')}</td>
                    <td>${r.direction}</td>
                    <td class="dim" style="font-size:.78rem">${r.received_at || r.sent_at || ''}</td>
                </tr>`).join('')}
            </table>
        ` : `
            <div class="card" style="text-align:center;padding:3rem">
                <p class="muted">No replies detected yet.</p>
                <button class="btn btn-primary" onclick="pollInbox()" style="margin-top:1rem">Poll inbox now</button>
            </div>
        `}
    `;
}

async function renderImport() {
    document.getElementById('main').innerHTML = `
        <h1 style="font-size:1.3rem;font-weight:800;margin-bottom:1.5rem">Import contacts</h1>
        <div class="card">
            <p class="muted" style="margin-bottom:1rem">Paste or upload a contact list. Supports TSV, CSV, pipe tables, and plain lines.</p>
            <form id="importForm">
                <label>Upload file</label>
                <input type="file" name="file" accept=".txt,.csv,.md,.tsv">
                <label>...or paste text</label>
                <textarea name="text" rows="10" placeholder="1&#9;Bigmiitch Events&#9;+254 724 214 461&#9;info@example.com&#9;🔥🔥🔥"></textarea>
                <div style="margin-top:1rem"><button type="submit" class="btn btn-primary">Import contacts</button></div>
            </form>
        </div>
        <div class="card" style="margin-top:1.5rem">
            <h2 style="font-size:1rem;font-weight:700;margin-bottom:.75rem">Create campaign</h2>
            <form id="campaignForm">
                <label>Campaign name</label>
                <input type="text" name="name" required placeholder="e.g. Taptap Oct/Nov">
                <label>Subject template</label>
                <input type="text" name="subject_template" required placeholder="e.g. Taptap for {{name}}">
                <label>Body template</label>
                <textarea name="body_template" rows="8" required placeholder="Hi {{first_name}},..."></textarea>
                <label>Follow-up 1 (days after initial)</label>
                <input type="number" name="follow_up_1_days" value="3">
                <textarea name="follow_up_1_body" rows="4" placeholder="Follow-up body..."></textarea>
                <label>Follow-up 2</label>
                <input type="number" name="follow_up_2_days" value="7">
                <textarea name="follow_up_2_body" rows="4" placeholder="Second follow-up..."></textarea>
                <div style="margin-top:1rem"><button type="submit" class="btn btn-primary">Create campaign</button></div>
            </form>
        </div>
        <div class="card" style="margin-top:1.5rem">
            <h2 style="font-size:1rem;font-weight:700;margin-bottom:.75rem">Verify emails</h2>
            <form id="verifyForm">
                <label>Email to verify</label>
                <input type="email" name="email" required placeholder="test@gmail.com">
                <div style="margin-top:1rem"><button type="submit" class="btn btn-primary btn-sm">Verify</button></div>
            </form>
            <div id="verifyResult" style="margin-top:1rem"></div>
        </div>
    `;
    document.getElementById('importForm').onsubmit = handleImport;
    document.getElementById('campaignForm').onsubmit = handleCampaign;
    document.getElementById('verifyForm').onsubmit = handleVerify;
}

async function renderAnalytics() {
    const data = await api('/api/analytics');
    document.getElementById('main').innerHTML = `
        <h1 style="font-size:1.3rem;font-weight:800;margin-bottom:1.5rem">Analytics</h1>
        <div class="stats">
            <div class="stat"><span class="stat-value">${data.total_leads || 0}</span><span class="stat-label">Total Leads</span></div>
            <div class="stat"><span class="stat-value blue">${data.total_sent || 0}</span><span class="stat-label">Emails Sent</span></div>
            <div class="stat"><span class="stat-value green">${data.total_replies || 0}</span><span class="stat-label">Replies</span></div>
            <div class="stat"><span class="stat-value purple">${data.total_interested || 0}</span><span class="stat-label">Interested</span></div>
        </div>
        <h2 style="font-size:1rem;font-weight:700;margin:1.5rem 0 .75rem">Daily sends (last 14 days)</h2>
        <div class="card">
            ${(data.daily_sends || []).map(d => `
            <div style="display:flex;justify-content:space-between;padding:.4rem 0;border-bottom:1px solid var(--border)">
                <span class="mono" style="font-size:.82rem">${d.day}</span>
                <span style="font-weight:600">${d.sent} sent</span>
            </div>`).join('')}
            ${!data.daily_sends?.length ? '<p class="muted" style="text-align:center;padding:2rem">No sends recorded yet.</p>' : ''}
        </div>
        <h2 style="font-size:1rem;font-weight:700;margin:1.5rem 0 .75rem">Campaign breakdown</h2>
        ${(data.campaigns || []).map(c => `
        <div class="card">
            <div class="card-title">
                <strong>${c.name || 'Campaign #' + c.campaign_id}</strong>
                <span class="dim" style="margin-left:auto;font-size:.78rem">Sent: ${c.messages_sent || 0} | Replies: ${c.stopped_replied || 0}</span>
            </div>
            ${c.reply_breakdown ? `<div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem">${Object.entries(c.reply_breakdown).map(([k, v]) => `<span class="badge badge-${k}">${k}: ${v}</span>`).join('')}</div>` : ''}
        </div>`).join('')}
    `;
}

function renderSettings() {
    document.getElementById('main').innerHTML = `
        <h1 style="font-size:1.3rem;font-weight:800;margin-bottom:1.5rem">Settings</h1>
        <div class="card">
            <form id="settingsForm">
                <label>Backend API URL</label>
                <input type="url" id="apiUrl" value="${API}" placeholder="http://localhost:8080">
                <label>Dashboard Token (optional)</label>
                <input type="password" id="dashToken" value="${TOKEN}" placeholder="Leave blank if no auth">
                <div style="margin-top:1rem;display:flex;gap:.5rem">
                    <button type="submit" class="btn btn-primary">Save</button>
                    <button type="button" class="btn btn-outline" onclick="testConnection()">Test connection</button>
                </div>
            </form>
            <div id="connectionTest" style="margin-top:1rem"></div>
        </div>
    `;
    document.getElementById('settingsForm').onsubmit = (e) => {
        e.preventDefault();
        localStorage.setItem('api_url', document.getElementById('apiUrl').value);
        localStorage.setItem('dashboard_token', document.getElementById('dashToken').value);
        toast('Settings saved');
        location.reload();
    };
}

function renderNotFound() {
    document.getElementById('main').innerHTML = '<div class="card" style="text-align:center;padding:4rem"><p class="muted">Page not found.</p></div>';
}

// ── Actions ───────────────────────────────────────────────────

async function handleImport(e) {
    e.preventDefault();
    const form = new FormData(e.target);
    const result = await apiForm('/api/import', form);
    if (result.ok) {
        toast(`Imported ${result.imported} contacts (${result.duplicates} duplicates)`);
        e.target.reset();
    } else {
        toast(result.detail || 'Import failed', 'error');
    }
}

async function handleCampaign(e) {
    e.preventDefault();
    const form = new FormData(e.target);
    const result = await apiForm('/api/campaigns/create', form);
    if (result.ok || result.campaign_id) {
        toast('Campaign created');
        e.target.reset();
    } else {
        toast(result.detail || 'Campaign creation failed', 'error');
    }
}

async function handleVerify(e) {
    e.preventDefault();
    const form = new FormData(e.target);
    const result = await apiPost('/api/verify-email', { email: form.get('email') });
    const el = document.getElementById('verifyResult');
    el.innerHTML = `
        <div class="card">
            <p><strong>Status:</strong> ${badge(result.status)}</p>
            <p><strong>MX valid:</strong> ${result.mx_valid ? '✅' : '❌'}</p>
            <p><strong>Disposable:</strong> ${result.disposable ? '⚠️ Yes' : '✅ No'}</p>
            <p><strong>Free provider:</strong> ${result.free_provider ? '📧 Yes' : '🏢 Business'}</p>
            <p><strong>Role account:</strong> ${result.role_account ? '⚠️ Yes (info@, admin@)' : '✅ No'}</p>
        </div>
    `;
}

async function activateCampaign(id) {
    await apiPost(`/api/campaigns/${id}/status`, { status: 'active' });
    toast('Campaign activated');
    renderCampaigns();
}

async function pauseCampaign(id) {
    await apiPost(`/api/campaigns/${id}/status`, { status: 'paused' });
    toast('Campaign paused');
    renderCampaigns();
}

async function runCampaign(id) {
    const result = await apiPost(`/api/campaigns/${id}/run-once`, {});
    toast(`Sent ${result.sent} emails (${result.failed} failed)`);
    renderCampaigns();
}

async function attachContacts(id) {
    const result = await apiPost(`/api/campaigns/${id}/attach`, {});
    toast(`Attached ${result.attached} contacts`);
    renderCampaigns();
}

async function moveStage(leadId, stage) {
    const form = new FormData();
    form.append('stage', stage);
    await apiForm(`/api/leads/${leadId}/stage`, form);
    toast(`Moved to ${stage}`);
}

async function pollInbox() {
    const result = await apiPost('/api/inbox/poll', {});
    toast(`Polled: ${result.matched} replies matched`);
}

async function testConnection() {
    try {
        const result = await api('/health');
        document.getElementById('connectionTest').innerHTML = `<p style="color:var(--green)">✅ Connected. DB: ${result.db}</p>`;
    } catch (e) {
        document.getElementById('connectionTest').innerHTML = `<p style="color:var(--red)">❌ Connection failed: ${e.message}</p>`;
    }
}

// ── Init ──────────────────────────────────────────────────────

renderNav();
window.addEventListener('hashchange', () => navigate(location.hash));
navigate(location.hash || '#/');
