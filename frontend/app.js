const BRAND = window.LR_BRAND || { name: 'LeadRadar', sub: 'Safe Pro', tagline: '', slug: 'leadradar' };
document.title = BRAND.name;

const CFG = {
  get api() { return localStorage.getItem('lr_api') || (location.origin + '/lr'); },
  set api(v) { localStorage.setItem('lr_api', v); },
  get token() { return localStorage.getItem('lr_token') || ''; },
  set token(v) { localStorage.setItem('lr_token', v); },
  get sess() { return localStorage.getItem('lr_sess') || ''; },
  set sess(v) { v ? localStorage.setItem('lr_sess', v) : localStorage.removeItem('lr_sess'); },
};

async function req(path, opts = {}) {
  const h = Object.assign({}, opts.headers);
  if (CFG.token) h['X-Dashboard-Token'] = CFG.token;
  if (CFG.sess) h['X-Session-Token'] = CFG.sess;
  let body = opts.body;
  if (body && !(body instanceof FormData)) { h['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  const r = await fetch(CFG.api + path, Object.assign({}, opts, { headers: h, body }));
  if (r.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new Event('lr-unauthorized'));
    throw new Error('Session expired');
  }
  if (!r.ok) {
    let msg = r.status + ' ' + r.statusText;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (e) {}
    throw new Error(msg);
  }
  return r;
}
const GET = p => req(p).then(r => r.json());
const POSTJ = (p, b) => req(p, { method: 'POST', body: b }).then(r => r.json());
const POSTF = (p, fd) => req(p, { method: 'POST', body: fd });

function timeago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'now';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}
function fmtDate(iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''; }
function copyTxt(t) { navigator.clipboard.writeText(t).catch(() => {}); }

function renderIcon(el, name) {
  el._i = name;
  const inner = (window.LR_ICONS && LR_ICONS[name]) || '';
  el.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';
}

const Automations = {
  load() { try { return Object.assign({ pollInbox: false, pollMins: 15, verifyImport: true, enrichHigh: false, autoRefresh: false }, JSON.parse(localStorage.getItem('lr_auto') || '{}')); } catch (e) { return {}; } },
  save(a) { localStorage.setItem('lr_auto', JSON.stringify(a)); },
};

const STAGES = ['new', 'contacted', 'replied', 'meeting', 'won', 'lost'];

const App = {
  data() {
    return {
      route: '/', conn: 'connecting',
      sideOpen: false,
      leads: [], campaigns: [], replies: [], analytics: {}, hooks: [],
      loadingLeads: false,
      q: '', fStatus: 'all', fStage: 'all', sortKey: 'updated_at', sortDir: -1,
      sel: [],
      modal: null,
      toasts: [],
      replyFilter: '',
      setTab: 'conn',
      cfgApi: CFG.api, cfgToken: CFG.token, testMsg: '', testOk: false,
      hook: { name: '', url: '', events: 'reply,interested' },
      auto: Automations.load(),
      stages: STAGES,
      origin: location.origin,
      sess: CFG.sess,
      needAuth: false,
      authRequired: false,
      authMode: 'login',
      authBusy: false,
      authErr: '',
      authMsg: '',
      authForm: { email: localStorage.getItem('lr_last_email') || '', password: '', otp: '', newPassword: '' },
      inboxMsgs: [],
      inboxLoading: false,
      inboxQ: '',
      _inboxSynced: false,
    };
  },
  computed: {
    navCounts() {
      return {
        '/leads': this.leads.length,
        '/campaigns': this.campaigns.filter(c => c.status === 'active').length || '',
        '/replies': this.replies.length,
      };
    },
    filteredLeads() {
      let rows = this.leads;
      const q = this.q.trim().toLowerCase();
      if (q && (this.route === '/leads' || this.route === '/')) rows = rows.filter(l => [l.name, l.email, l.phone, l.event_name].some(v => (v || '').toLowerCase().includes(q)));
      if (this.fStatus !== 'all') rows = rows.filter(l => l.status === this.fStatus);
      if (this.fStage !== 'all') rows = rows.filter(l => (l.pipeline_stage || 'new') === this.fStage);
      const k = this.sortKey, d = this.sortDir;
      return [...rows].sort((a, b) => ((a[k] || '') < (b[k] || '') ? -1 : (a[k] || '') > (b[k] || '') ? 1 : 0) * d);
    },
    statusCounts() {
      const c = { all: this.leads.length };
      this.leads.forEach(l => c[l.status] = (c[l.status] || 0) + 1);
      return c;
    },
    stageCounts() {
      const c = {};
      this.leads.forEach(l => { const s = l.pipeline_stage || 'new'; c[s] = (c[s] || 0) + 1; });
      return c;
    },
    kpis() {
      const L = this.leads;
      return {
        total: L.length,
        sent: L.filter(l => l.status === 'sent').length,
        approved: L.filter(l => l.status === 'approved').length,
        replied: L.filter(l => ['replied', 'meeting'].includes(l.pipeline_stage)).length,
        interested: this.replies.filter(r => r.keyword === 'interested').length,
        high: L.filter(l => l.priority === 'high').length,
      };
    },
    replyGroups() {
      const g = {};
      this.replies.forEach(r => { const k = r.keyword || 'unknown'; g[k] = (g[k] || 0) + 1; });
      return g;
    },
    shownReplies() {
      if (!this.replyFilter) return this.replies;
      return this.replies.filter(r => (r.keyword || 'unknown') === this.replyFilter);
    },
    boardCols() {
      const cols = {};
      STAGES.forEach(s => cols[s] = []);
      this.leads.forEach(l => { const s = l.pipeline_stage || 'new'; (cols[s] || (cols[s] = [])).push(l); });
      return cols;
    },
    maxDaily() { return Math.max(1, ...(this.analytics.daily_sends || []).map(d => d.sent)); },
  },
  mounted() {
    window.addEventListener('hashchange', () => this.onRoute());
    window.addEventListener('lr-unauthorized', () => this.forceLogin());
    window.addEventListener('keydown', e => {
      if (e.key === 'Escape' && this.modal) this.modal = null;
      if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); const s = this.$refs.search; s && s.focus(); }
    });
    this.boot();
    setInterval(() => { this.auto = Automations.load(); }, 4000);
    setInterval(() => { if (this.auto.pollInbox) { this.pollInbox(true); this._inboxSilent = true; this.syncInbox(); this._inboxSilent = false; } }, 15 * 60000);
    setInterval(() => { if (this.auto.autoRefresh && ['/','/leads','/campaigns','/replies','/inbox'].includes(this.route)) this.routeData().catch(()=>{}); }, 30000);
  },
  methods: {
    async boot() {
      try {
        const h = await GET('/health');
        this.authRequired = !!h.auth_required;
        this.conn = 'ok';
      } catch (e) { this.conn = 'err'; }
      if (this.authRequired && !this.sess) { this.needAuth = true; return; }
      if (this.sess) {
        try { await GET('/api/auth/me'); }
        catch (e) { this.sess = ''; CFG.sess = ''; this.needAuth = true; return; }
      }
      this.onRoute();
    },
    forceLogin() {
      this.sess = ''; CFG.sess = '';
      this.needAuth = true;
      this.modal = null;
    },
    async doLogin() {
      this.authBusy = true; this.authErr = '';
      try {
        const r = await POSTJ('/api/auth/login', { email: this.authForm.email, password: this.authForm.password });
        CFG.sess = r.token;
        localStorage.setItem('lr_last_email', this.authForm.email);
        this.sess = r.token;
        this.needAuth = false;
        this.conn = 'ok';
        this.onRoute();
        this.toast('Welcome back');
      } catch (e) { this.authErr = e.message; }
      this.authBusy = false;
    },
    async doForgot() {
      this.authBusy = true; this.authErr = ''; this.authMsg = '';
      try {
        const r = await POSTJ('/api/auth/forgot', { email: this.authForm.email });
        localStorage.setItem('lr_last_email', this.authForm.email);
        this.authMsg = r.message || 'Draft created';
        this.authMode = 'reset';
      } catch (e) { this.authErr = e.message; }
      this.authBusy = false;
    },
    async doReset() {
      this.authBusy = true; this.authErr = '';
      try {
        const r = await POSTJ('/api/auth/reset', { email: this.authForm.email, otp: this.authForm.otp, new_password: this.authForm.newPassword });
        this.authMsg = r.message || 'Password updated';
        this.authForm.password = '';
        this.authMode = 'login';
      } catch (e) { this.authErr = e.message; }
      this.authBusy = false;
    },
    async doLogout() {
      try { await req('/api/auth/logout', { method: 'POST', body: {} }); } catch (e) {}
      this.forceLogin();
      this.authMode = 'login';
      this.authForm.password = '';
    },
    async syncInbox() {
      this.inboxLoading = true;
      try {
        const r = await POSTJ('/api/inbox/sync', {});
        await this.loadInbox();
        if (!this._inboxSilent) this.toast('Synced ' + r.fetched + ' emails (' + r.new + ' new)');
        this._inboxSynced = true;
      } catch (e) { if (!this._inboxSilent) this.toast(e.message, true); }
      this.inboxLoading = false;
    },
    async loadInbox() {
      try { this.inboxMsgs = (await GET('/api/inbox?limit=100')).messages || []; } catch (e) {}
    },
    filteredInbox() {
      const q = this.inboxQ.trim().toLowerCase();
      if (!q) return this.inboxMsgs;
      return this.inboxMsgs.filter(m => [m.from_name, m.from_email, m.subject, m.snippet].some(v => (v || '').toLowerCase().includes(q)));
    },
    openLeadById(id) {
      const l = this.leads.find(x => x.id === id);
      if (l) this.openLead(l); else this.go('/leads');
    },
    go(r) { location.hash = '#' + r; },
    searchFocus() { if (!['/', '/leads'].includes(this.route)) this.go('/leads'); },
    onRoute() {
      this.route = location.hash.replace('#', '') || '/';
      this.sideOpen = false;
      if (this.needAuth) return;
      this.routeData().catch(e => this.toast(e.message, true));
    },
    toast(msg, err) {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, msg, err });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 3400);
    },
    async routeData() {
      const r = this.route;
      if (r === '/') await Promise.all([this.loadLeads(true), this.loadCampaigns(true), this.loadReplies(true)]), this.maybeEnrich();
      else if (r === '/leads' || r === '/pipeline') await this.loadLeads();
      else if (r === '/campaigns') await this.loadCampaigns(true);
      else if (r === '/replies') await this.loadReplies();
      else if (r === '/inbox') {
        await this.loadInbox();
        if (!this._inboxSynced && !this.inboxMsgs.length) { this._inboxSilent = true; await this.syncInbox(); this._inboxSilent = false; }
      }
      else if (r === '/analytics') { this.analytics = await GET('/api/analytics'); await this.loadCampaigns(true); }
    },
    async loadLeads(silent) {
      if (!silent) this.loadingLeads = true;
      try { this.leads = (await GET('/api/leads?limit=1000')).leads || []; } finally { this.loadingLeads = false; }
    },
    async loadCampaigns(silent) { this.campaigns = (await GET('/api/campaigns')).campaigns || []; },
    async loadReplies(silent) { this.replies = ((await GET('/api/replies')).replies || []).slice().reverse(); },
    setSort(k) { if (this.sortKey === k) this.sortDir *= -1; else { this.sortKey = k; this.sortDir = 1; } },
    toggleSel(id) { this.sel = this.sel.includes(id) ? this.sel.filter(i => i !== id) : [...this.sel, id]; },
    selAll(e) { this.sel = e.target.checked ? this.filteredLeads.map(l => l.id) : []; },

    openModal(type, data) { this.modal = { type, data }; },
    close() { this.modal = null; },
    openLead(l) { this.openModal('lead', l); },
    openCampDetail(c) { this.openModal('campdetail', c); },

    progPct(c) {
      const a = c.analytics || {};
      return a.targets ? Math.min(100, Math.round((a.finished / a.targets) * 100)) : 0;
    },
    chartH(v) { return Math.max(3, Math.round((v / this.maxDaily) * 100)); },

    async campAction(c, act) {
      try {
        if (act === 'run') { const r = await POSTJ('/api/campaigns/' + c.id + '/run-once', {}); this.toast('Sent ' + (r.sent || 0) + ', failed ' + (r.failed || 0)); }
        else if (act === 'activate') { await POSTJ('/api/campaigns/' + c.id + '/status', { status: 'active' }); this.toast('Activated'); }
        else if (act === 'pause') { await POSTJ('/api/campaigns/' + c.id + '/status', { status: 'paused' }); this.toast('Paused'); }
        else if (act === 'attach') { const r = await POSTJ('/api/campaigns/' + c.id + '/attach', {}); this.toast('Attached ' + (r.attached || 0)); }
        await this.loadCampaigns(true);
      } catch (e) { this.toast(e.message, true); }
    },
    async moveStage(l, stage) {
      const fd = new FormData(); fd.append('stage', stage);
      await POSTF('/api/leads/' + l.id + '/stage', fd);
      l.pipeline_stage = stage;
      this.toast('Moved to ' + stage);
    },
    async pollInbox(quiet) {
      try {
        const r = await POSTJ('/api/inbox/poll', {});
        if (!quiet) this.toast('Polled: ' + (r.matched || 0) + ' matched');
        await this.loadReplies(true);
      } catch (e) { if (!quiet) this.toast('Poll failed: ' + e.message, true); }
    },
    async verifyAll() {
      try {
        const emails = this.leads.map(l => l.email).filter(Boolean).slice(0, 100);
        if (!emails.length) return this.toast('No emails to verify', true);
        const r = await POSTJ('/api/verify-batch', { emails });
        const c = {};
        (r.results || []).forEach(x => c[x.status] = (c[x.status] || 0) + 1);
        this.toast('Verified — ' + Object.entries(c).map(([k, v]) => k + ' ' + v).join(', '));
      } catch (e) { this.toast('Verify failed: ' + e.message, true); }
    },
    async maybeEnrich() {
      if (!this.auto.enrichHigh) return;
      const targets = this.leads.filter(l => l.priority === 'high' && !l.website_url && !(this._enr || (this._enr = new Set())).has(l.id)).slice(0, 5);
      if (!targets.length) return;
      for (const t of targets) { try { await POSTF('/api/leads/' + t.id + '/enrich', new FormData()); this._enr.add(t.id); } catch (e) {} }
      this.toast('Enriched ' + targets.length + ' leads');
      await this.loadLeads(true);
    },
    async bulkVerify() {
      const emails = this.leads.filter(l => this.sel.includes(l.id)).map(l => l.email).filter(Boolean);
      if (!emails.length) return;
      try {
        const r = await POSTJ('/api/verify-batch', { emails });
        const c = {};
        (r.results || []).forEach(x => c[x.status] = (c[x.status] || 0) + 1);
        this.toast('Verified — ' + Object.entries(c).map(([k, v]) => k + ' ' + v).join(', '));
      } catch (e) { this.toast(e.message, true); }
    },
    async bulkStage(e) {
      const stage = e.target.value;
      if (!stage) return;
      for (const id of this.sel) {
        const l = this.leads.find(x => x.id === id);
        if (l) await this.moveStage(l, stage);
      }
      e.target.value = '';
    },
    exportCsv(rows) {
      rows = rows && rows.length ? rows : this.filteredLeads;
      const cols = ['name', 'email', 'phone', 'status', 'pipeline_stage', 'priority', 'event_name'];
      const csv = [cols.join(',')].concat(rows.map(l => cols.map(c => '"' + String(l[c] || '').replace(/"/g, '""') + '"').join(','))).join('\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
      a.download = BRAND.slug + '-leads.csv'; a.click();
      this.toast('Exported ' + rows.length + ' leads');
    },
    saveConn() {
      CFG.api = this.cfgApi.replace(/\/$/, '');
      CFG.token = this.cfgToken;
      this.testMsg = ''; this.conn = 'connecting';
      GET('/health').then(() => { this.conn = 'ok'; this.toast('Connected'); }).catch(e => { this.conn = 'err'; this.toast(e.message, true); });
    },
    async testConn() {
      try { const h = await GET('/health'); this.testOk = true; this.testMsg = 'Connected — db: ' + h.db; }
      catch (e) { this.testOk = false; this.testMsg = e.message; }
    },
    saveAuto() { Automations.save(this.auto); this.toast('Automations saved'); },
    async loadHooks() { try { this.hooks = (await GET('/api/webhooks')).webhooks || []; } catch (e) {} },
    async addHook() {
      try {
        const fd = new FormData();
        fd.append('name', this.hook.name); fd.append('url', this.hook.url); fd.append('events', this.hook.events);
        await POSTF('/api/webhooks', fd);
        this.hook = { name: '', url: '', events: 'reply,interested' };
        await this.loadHooks();
        this.toast('Webhook added');
      } catch (e) { this.toast(e.message, true); }
    },
    afterImportDone() {
      this.routeData().catch(() => {});
      if (this.auto.verifyImport) {
        const fresh = this.leads.map(l => l.email).filter(Boolean).slice(0, 60);
        if (fresh.length) POSTJ('/api/verify-batch', { emails: fresh }).then(r => {
          const c = {}; (r.results || []).forEach(x => c[x.status] = (c[x.status] || 0) + 1);
          this.toast('Auto-verified: ' + Object.entries(c).map(([k, v]) => k + ' ' + v).join(', '));
        }).catch(() => {});
      }
    },
  },
  template: `
<div class="shell" v-if="!needAuth">
  <aside class="side" :class="{open:sideOpen}">
    <div class="brand">
      <div class="brand-mark"><i v-ic="'radar'"></i></div>
      <div><div class="brand-name">${BRAND.name}</div><div class="brand-sub">${BRAND.sub}</div></div>
    </div>
    <nav class="nav">
      <div class="nav-label">Workspace</div>
      <a href="#/" class="nav-item" :class="{on:route==='/'}"><i v-ic="'layout-dashboard'"></i> Dashboard</a>
      <a href="#/leads" class="nav-item" :class="{on:route==='/leads'}"><i v-ic="'inbox'"></i> Leads <span class="n-count" v-if="navCounts['/leads']">{{navCounts['/leads']}}</span></a>
      <a href="#/campaigns" class="nav-item" :class="{on:route==='/campaigns'}"><i v-ic="'send'"></i> Campaigns</a>
      <a href="#/pipeline" class="nav-item" :class="{on:route==='/pipeline'}"><i v-ic="'square-kanban'"></i> Pipeline</a>
      <a href="#/replies" class="nav-item" :class="{on:route==='/replies'}"><i v-ic="'message-square'"></i> Replies <span class="n-count" v-if="navCounts['/replies']">{{navCounts['/replies']}}</span></a>
      <a href="#/inbox" class="nav-item" :class="{on:route==='/inbox'}"><i v-ic="'mail'"></i> Inbox <span class="n-count" v-if="inboxMsgs.length">{{inboxMsgs.length}}</span></a>
      <a href="#/analytics" class="nav-item" :class="{on:route==='/analytics'}"><i v-ic="'bar-chart-3'"></i> Analytics</a>
      <div class="nav-label">System</div>
      <a href="#/settings" class="nav-item" :class="{on:route==='/settings'}"><i v-ic="'settings'"></i> Settings</a>
    </nav>
    <div class="side-foot">
      <div class="conn">
        <span class="pulse" :class="{ok:conn==='ok',err:conn==='err'}"></span>
        <div style="min-width:0">
          <div class="conn-txt">{{conn==='ok'?'Connected':conn==='err'?'Offline':'Connecting…'}}</div>
          <div class="conn-url">{{cfgApi}}</div>
        </div>
        <button class="icon-btn" title="Sign out" @click="doLogout"><i v-ic="'log-out'"></i></button>
      </div>
    </div>
  </aside>

  <div class="wrap">
    <header class="top">
      <button class="burger icon-btn" @click="sideOpen=!sideOpen"><i v-ic="'filter'"></i></button>
      <div class="search">
        <i v-ic="'search'"></i>
        <input ref="search" v-model="q" placeholder="Search leads…   ( / )" @focus="searchFocus" @keydown.enter="go('/leads')">
      </div>
      <div class="top-spacer"></div>
      <div class="top-actions">
        <button class="btn btn-g btn-sm" @click="openModal('import')"><i v-ic="'download'"></i> Import</button>
        <button class="btn btn-p btn-sm" @click="openModal('newcamp')"><i v-ic="'plus'"></i> Campaign</button>
      </div>
    </header>

    <main class="page">

      <template v-if="route==='/'">
        <div class="page-head">
          <div><div class="page-title">Mission control</div><div class="page-desc">{{leads.length}} contacts · {{campaigns.length}} campaigns · {{replies.length}} replies tracked</div></div>
          <button class="btn btn-o btn-sm" @click="pollInbox()"><i v-ic="'refresh-cw'"></i> Sync inbox</button>
        </div>
        <div class="kpis">
          <button class="kpi clickable" style="--k:var(--blue)" @click="go('/leads')"><div class="kpi-v">{{kpis.total}}</div><div class="kpi-l">Total leads</div></button>
          <button class="kpi clickable" style="--k:var(--acc)" @click="go('/campaigns')"><div class="kpi-v">{{kpis.sent}}</div><div class="kpi-l">Emails sent</div></button>
          <button class="kpi clickable" style="--k:var(--amber)" @click="go('/leads')"><div class="kpi-v">{{kpis.approved}}</div><div class="kpi-l">Awaiting send</div></button>
          <button class="kpi clickable" style="--k:var(--blue)" @click="go('/replies')"><div class="kpi-v">{{kpis.replied}}</div><div class="kpi-l">Replied</div></button>
          <button class="kpi clickable" style="--k:var(--acc)" @click="go('/replies')"><div class="kpi-v">{{kpis.interested}}</div><div class="kpi-l">Interested</div></button>
          <button class="kpi clickable" style="--k:var(--red)" @click="go('/leads')"><div class="kpi-v">{{kpis.high}}</div><div class="kpi-l">High priority</div></button>
        </div>
        <div class="dash-grid">
          <div class="panel">
            <div class="panel-h"><span class="panel-t">Latest replies</span><a href="#/replies" class="panel-s">all →</a></div>
            <div style="padding:4px 18px 12px">
              <div v-for="r in replies.slice(0,6)" :key="r.id||Math.random()" class="rep-row">
                <span class="bg" :class="'bg-'+(r.keyword||'unknown')">{{r.keyword||'unknown'}}</span>
                <span class="mono dim rep-mail">{{r.from_email||r.to_email}}</span>
                <span class="act-time">{{ago(r.received_at||r.sent_at)}}</span>
              </div>
              <div v-if="!replies.length" class="empty" style="padding:22px"><div class="empty-d">No replies yet.</div></div>
            </div>
          </div>
          <div class="panel">
            <div class="panel-h"><span class="panel-t">Campaigns</span><a href="#/campaigns" class="panel-s">manage →</a></div>
            <div style="padding:8px 18px 16px">
              <div v-for="c in campaigns" :key="c.id" style="padding:9px 0;border-bottom:1px solid var(--line)">
                <div class="cam-row">
                  <span class="cell-name" style="font-size:.84rem">{{c.name}}</span>
                  <span class="bg" :class="'bg-'+c.status">{{c.status}}</span>
                </div>
                <div class="prog" style="margin-top:7px"><i :style="{width:progPct(c)+'%'}"></i></div>
                <div class="dim mono" style="font-size:.62rem;margin-top:4px">{{(c.analytics&&c.analytics.messages_sent)||0}} sent · {{progPct(c)}}% complete</div>
              </div>
              <div v-if="!campaigns.length" class="empty" style="padding:22px"><div class="empty-d">No campaigns yet.</div></div>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/leads'">
        <div class="page-head">
          <div><div class="page-title">Leads</div><div class="page-desc">{{filteredLeads.length}} of {{leads.length}} shown</div></div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-g btn-sm" @click="verifyAll"><i v-ic="'shield-check'"></i> Verify all</button>
            <button class="btn btn-o btn-sm" @click="exportCsv"><i v-ic="'download'"></i> Export CSV</button>
          </div>
        </div>
        <div class="chips" style="margin-bottom:12px">
          <button class="chip" :class="{on:fStatus==='all'}" @click="fStatus='all'">All<span class="c-n">{{statusCounts.all}}</span></button>
          <template v-for="(n,s) in statusCounts" :key="s">
            <button v-if="s!=='all'" class="chip" :class="{on:fStatus===s}" @click="fStatus=s">{{s}}<span class="c-n">{{n}}</span></button>
          </template>
          <select v-model="fStage" class="mini-sel">
            <option value="all">All stages</option>
            <option v-for="(n,s) in stageCounts" :key="s" :value="s">{{s}} ({{n}})</option>
          </select>
        </div>
        <div class="bulkbar" v-if="sel.length">
          <span class="cnt">{{sel.length}} selected</span>
          <button class="btn btn-g btn-sm" @click="bulkVerify"><i v-ic="'shield-check'"></i> Verify</button>
          <select class="mini-sel" @change="bulkStage($event)">
            <option value="">Move to stage…</option>
            <option v-for="s in stages" :key="s" :value="s">{{s}}</option>
          </select>
          <button class="btn btn-o btn-sm" @click="exportCsv(leads.filter(l=>sel.includes(l.id)))"><i v-ic="'download'"></i> Export</button>
          <button class="btn btn-o btn-sm" @click="sel=[]">Clear</button>
        </div>
        <div class="panel">
          <table class="tbl" v-if="filteredLeads.length">
            <thead><tr>
              <th style="width:34px"><input type="checkbox" @change="selAll($event)" :checked="sel.length&&sel.length===filteredLeads.length"></th>
              <th class="sortable" @click="setSort('name')">Name</th>
              <th class="sortable" @click="setSort('email')">Email</th>
              <th class="sortable" @click="setSort('priority')">Priority</th>
              <th class="sortable" @click="setSort('status')">Status</th>
              <th class="sortable" @click="setSort('pipeline_stage')">Stage</th>
              <th class="sortable" @click="setSort('updated_at')">Updated</th>
            </tr></thead>
            <tbody>
              <tr v-for="l in filteredLeads" :key="l.id" @click="openLead(l)">
                <td @click.stop><input type="checkbox" :checked="sel.includes(l.id)" @change="toggleSel(l.id)"></td>
                <td><div class="cell-name">{{l.name}}</div><div class="cell-sub" v-if="l.event_name">{{l.event_name}}</div></td>
                <td class="mono mut">{{l.email}}</td>
                <td><span class="bg" :class="'bg-'+(l.priority||'low')">{{l.priority||'low'}}</span></td>
                <td><span class="bg" :class="'bg-'+l.status">{{l.status}}</span></td>
                <td class="mono dim">{{l.pipeline_stage||'new'}}</td>
                <td class="mono dim">{{ago(l.updated_at)}}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="loadingLeads&&!filteredLeads.length" style="padding:18px">
            <div class="skel" style="height:40px;margin-bottom:8px"></div><div class="skel" style="height:40px;margin-bottom:8px"></div><div class="skel" style="height:40px"></div>
          </div>
          <div v-if="!loadingLeads&&!filteredLeads.length" class="empty">
            <div class="empty-ic"><i v-ic="'inbox'"></i></div>
            <div class="empty-t">Nothing here</div>
            <div class="empty-d">Adjust filters or import a fresh contact sheet.</div>
            <div class="empty-a"><button class="btn btn-p btn-sm" @click="openModal('import')"><i v-ic="'download'"></i> Import contacts</button></div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/campaigns'">
        <div class="page-head">
          <div><div class="page-title">Campaigns</div><div class="page-desc">Sequenced outreach · approval gates · rate limits</div></div>
          <button class="btn btn-p btn-sm" @click="openModal('newcamp')"><i v-ic="'plus'"></i> New campaign</button>
        </div>
        <div v-if="!campaigns.length" class="panel"><div class="empty">
          <div class="empty-ic"><i v-ic="'send'"></i></div>
          <div class="empty-t">No campaigns yet</div>
          <div class="empty-d">Build your first sequence — initial email plus scheduled follow-ups.</div>
        </div></div>
        <div v-for="c in campaigns" :key="c.id" class="panel camp-panel">
          <div class="panel-h">
            <div style="min-width:0">
              <span class="panel-t">{{c.name}}</span>
              <span class="bg" :class="'bg-'+c.status" style="margin-left:10px">{{c.status}}</span>
              <div class="mono dim camp-subj">{{c.subject_template}}</div>
            </div>
            <div class="camp-actions">
              <button v-if="c.status!=='active'" class="btn btn-p btn-sm" @click="campAction(c,'activate')"><i v-ic="'play'"></i> Activate</button>
              <template v-else>
                <button class="btn btn-g btn-sm" @click="campAction(c,'run')"><i v-ic="'play'"></i> Run</button>
                <button class="btn btn-o btn-sm" @click="campAction(c,'pause')"><i v-ic="'pause'"></i></button>
              </template>
              <button class="btn btn-o btn-sm" @click="campAction(c,'attach')"><i v-ic="'link-2'"></i> Attach</button>
              <button class="icon-btn" title="Details" @click="openCampDetail(c)"><i v-ic="'eye'"></i></button>
            </div>
          </div>
          <div class="camp-stats">
            <div><div class="kpi-v cs-v">{{(c.analytics&&c.analytics.targets)||0}}</div><div class="kpi-l">targets</div></div>
            <div><div class="kpi-v cs-v" style="color:var(--blue)">{{(c.analytics&&c.analytics.messages_sent)||0}}</div><div class="kpi-l">sent</div></div>
            <div><div class="kpi-v cs-v" style="color:var(--amber)">{{(c.analytics&&c.analytics.stopped_replied)||0}}</div><div class="kpi-l">replies</div></div>
            <div style="flex:1;min-width:150px">
              <div class="prog"><i :style="{width:progPct(c)+'%'}"></i></div>
              <div class="dim mono" style="font-size:.62rem;margin-top:4px">{{(c.analytics&&c.analytics.finished)||0}}/{{(c.analytics&&c.analytics.targets)||0}} reached</div>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/pipeline'">
        <div class="page-head">
          <div><div class="page-title">Pipeline</div><div class="page-desc">Move deals forward — change stage inline.</div></div>
        </div>
        <div class="board">
          <div class="col" v-for="s in stages" :key="s">
            <div class="col-h"><span class="col-t">{{s}}</span><span class="col-n">{{(boardCols[s]||[]).length}}</span></div>
            <div class="kcard" v-for="l in (boardCols[s]||[]).slice(0,30)" :key="l.id" @click="openLead(l)">
              <div class="kcard-n">{{l.name}}</div>
              <div class="kcard-e">{{l.email}}</div>
              <div class="kcard-ev" v-if="l.event_name"><i v-ic="'calendar'"></i>{{l.event_name}}</div>
              <select :value="s" @click.stop @change="moveStage(l,$event.target.value)">
                <option v-for="st in stages" :key="st" :value="st">{{st}}</option>
              </select>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/replies'">
        <div class="page-head">
          <div><div class="page-title">Replies</div><div class="page-desc">Classified automatically on every inbox poll.</div></div>
          <button class="btn btn-g btn-sm" @click="pollInbox()"><i v-ic="'refresh-cw'"></i> Poll inbox</button>
        </div>
        <div class="chips" style="margin-bottom:12px">
          <button class="chip" :class="{on:!replyFilter}" @click="replyFilter=''">All<span class="c-n">{{replies.length}}</span></button>
          <template v-for="(n,k) in replyGroups" :key="k">
            <button class="chip" :class="{on:replyFilter===k}" @click="replyFilter=k">{{k}}<span class="c-n">{{n}}</span></button>
          </template>
        </div>
        <div class="panel">
          <table class="tbl" v-if="shownReplies.length">
            <thead><tr><th>Contact</th><th>Email</th><th>Classification</th><th>Direction</th><th>When</th></tr></thead>
            <tbody>
              <tr v-for="(r,i) in shownReplies" :key="i">
                <td class="cell-name">{{r.lead_name||'Unknown'}}</td>
                <td class="mono mut">{{r.from_email||r.to_email}}</td>
                <td><span class="bg" :class="'bg-'+(r.keyword||'unknown')">{{r.keyword||'unknown'}}</span></td>
                <td class="mono dim">{{r.direction}}</td>
                <td class="mono dim">{{ago(r.received_at||r.sent_at)}}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty">
            <div class="empty-ic"><i v-ic="'message-square'"></i></div>
            <div class="empty-t">No replies{{replyFilter?' tagged '+replyFilter:''}}</div>
            <div class="empty-d">IMAP credentials live server-side; poll to classify incoming mail.</div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/inbox'">
        <div class="page-head">
          <div><div class="page-title">Inbox</div><div class="page-desc">{{inboxMsgs.length}} emails mirrored from your Gmail · newest first</div></div>
          <button class="btn btn-g btn-sm" @click="syncInbox()" :disabled="inboxLoading"><i v-ic="'refresh-cw'"></i> {{inboxLoading?'Syncing…':'Sync now'}}</button>
        </div>
        <div class="search" style="max-width:none;margin-bottom:12px">
          <i v-ic="'search'"></i>
          <input v-model="inboxQ" placeholder="Filter by sender, subject, content…">
        </div>
        <div class="panel">
          <div v-for="m in filteredInbox()" :key="m.message_id" class="mail-row" @click="m.lead_id&&openLeadById(m.lead_id)" :class="{linked:m.lead_id}">
            <div class="mail-top">
              <span class="mail-from">{{m.from_name||m.from_email}}</span>
              <span class="bg bg-new" v-if="m.lead_id" title="Matched to a lead">lead</span>
              <span class="mail-date mono dim">{{ago(m.date_utc)}}</span>
            </div>
            <div class="mail-subject">{{m.subject}}</div>
            <div class="mail-snip dim" v-if="m.snippet">{{m.snippet}}</div>
          </div>
          <div v-if="inboxLoading&&!inboxMsgs.length" style="padding:18px">
            <div class="skel" style="height:56px;margin-bottom:8px"></div><div class="skel" style="height:56px;margin-bottom:8px"></div><div class="skel" style="height:56px"></div>
          </div>
          <div v-if="!inboxLoading&&!filteredInbox().length" class="empty">
            <div class="empty-ic"><i v-ic="'mail'"></i></div>
            <div class="empty-t">No mail mirrored yet</div>
            <div class="empty-d">Hit Sync now — the latest 60 messages from your INBOX are pulled in and matched to leads.</div>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/analytics'">
        <div class="page-head"><div><div class="page-title">Analytics</div><div class="page-desc">Volume, reply quality, per-campaign breakdown.</div></div></div>
        <div class="kpis">
          <div class="kpi" style="--k:var(--blue)"><div class="kpi-v">{{analytics.total_leads||0}}</div><div class="kpi-l">Leads</div></div>
          <div class="kpi" style="--k:var(--acc)"><div class="kpi-v">{{analytics.total_sent||0}}</div><div class="kpi-l">Sent</div></div>
          <div class="kpi" style="--k:var(--amber)"><div class="kpi-v">{{analytics.total_replies||0}}</div><div class="kpi-l">Replies</div></div>
          <div class="kpi" style="--k:var(--acc)"><div class="kpi-v">{{analytics.total_interested||0}}</div><div class="kpi-l">Interested</div></div>
        </div>
        <div class="panel" style="margin-bottom:14px">
          <div class="panel-h"><span class="panel-t">Sends · last 14 days</span></div>
          <div style="padding:16px 18px 10px">
            <div class="chart-bars" v-if="(analytics.daily_sends||[]).length">
              <div class="chart-col" v-for="d in analytics.daily_sends" :key="d.day">
                <div class="chart-bar" :style="{height:chartH(d.sent)+'%'}" :title="d.day+': '+d.sent+' sent'"></div>
                <div class="chart-x">{{fmtDate(d.day)}}</div>
              </div>
            </div>
            <div v-else class="empty" style="padding:28px"><div class="empty-d">No sends recorded yet.</div></div>
          </div>
        </div>
        <div class="panel" v-for="c in (analytics.campaigns||[])" :key="c.campaign_id" style="margin-bottom:12px">
          <div class="panel-h">
            <span class="panel-t">{{c.name}}</span>
            <span class="mono dim" style="font-size:.68rem">sent {{c.messages_sent||0}} · replies {{c.stopped_replied||0}}</span>
          </div>
          <div style="padding:10px 18px 14px;display:flex;gap:8px;flex-wrap:wrap">
            <span v-for="(v,k) in (c.reply_breakdown||{})" :key="k" class="bg" :class="'bg-'+k">{{k}} {{v}}</span>
            <span v-if="!Object.keys(c.reply_breakdown||{}).length" class="dim" style="font-size:.76rem">No replies classified yet.</span>
          </div>
        </div>
      </template>

      <template v-else-if="route==='/settings'">
        <div class="page-head"><div><div class="page-title">Settings</div><div class="page-desc">Connection, automations, outbound webhooks.</div></div></div>
        <div class="panel" style="max-width:720px">
          <div class="tabs">
            <button class="tab" :class="{on:setTab==='conn'}" @click="setTab='conn'"><i v-ic="'plug'"></i> Connection</button>
            <button class="tab" :class="{on:setTab==='auto'}" @click="setTab='auto'"><i v-ic="'zap'"></i> Automations</button>
            <button class="tab" :class="{on:setTab==='hook'}" @click="setTab='hook';loadHooks()"><i v-ic="'webhook'"></i> Webhooks</button>
          </div>
          <div v-if="setTab==='conn'" style="padding:14px 20px 20px">
            <label class="fl">Backend URL</label>
            <input type="url" v-model="cfgApi">
            <p class="hint">Default <span class="mono">{{origin}}/lr</span> — point anywhere ${BRAND.name} runs.</p>
            <label class="fl">Dashboard token</label>
            <input type="password" v-model="cfgToken" placeholder="Only if DASHBOARD_TOKEN is set server-side">
            <div style="display:flex;gap:8px;margin-top:16px">
              <button class="btn btn-p btn-sm" @click="saveConn"><i v-ic="'check'"></i> Save & connect</button>
              <button class="btn btn-g btn-sm" @click="testConn"><i v-ic="'refresh-cw'"></i> Test</button>
            </div>
            <p v-if="testMsg" class="hint" :style="{color:testOk?'var(--acc)':'var(--red)'}">{{testMsg}}</p>
          </div>
          <div v-if="setTab==='auto'" style="padding:2px 20px 14px">
            <div class="tog">
              <div class="tog-info"><div class="tog-name">Poll inbox automatically</div><div class="tog-desc">Check Gmail over IMAP every 15 minutes and classify replies.</div></div>
              <label class="switch"><input type="checkbox" v-model="auto.pollInbox" @change="saveAuto"><span class="sw"></span></label>
            </div>
            <div class="tog">
              <div class="tog-info"><div class="tog-name">Verify after import</div><div class="tog-desc">Runs deliverability checks on newly imported emails.</div></div>
              <label class="switch"><input type="checkbox" v-model="auto.verifyImport" @change="saveAuto"><span class="sw"></span></label>
            </div>
            <div class="tog">
              <div class="tog-info"><div class="tog-name">Enrich high-priority leads</div><div class="tog-desc">Fetches website, socials and tech stack — up to 5 per dashboard visit.</div></div>
              <label class="switch"><input type="checkbox" v-model="auto.enrichHigh" @change="saveAuto"><span class="sw"></span></label>
            </div>
            <div class="tog">
              <div class="tog-info"><div class="tog-name">Auto-refresh views</div><div class="tog-desc">Reload dashboard data every 30 seconds.</div></div>
              <label class="switch"><input type="checkbox" v-model="auto.autoRefresh" @change="saveAuto"><span class="sw"></span></label>
            </div>
            <div style="display:flex;gap:8px;margin-top:16px">
              <button class="btn btn-g btn-sm" @click="pollInbox()"><i v-ic="'refresh-cw'"></i> Poll now</button>
              <button class="btn btn-g btn-sm" @click="verifyAll"><i v-ic="'shield-check'"></i> Verify all now</button>
            </div>
          </div>
          <div v-if="setTab==='hook'" style="padding:14px 20px 20px">
            <form @submit.prevent="addHook">
              <div class="frow">
                <div><label class="fl">Name</label><input type="text" v-model="hook.name" required placeholder="Slack alerts"></div>
                <div><label class="fl">Events</label><select v-model="hook.events"><option value="reply,interested">Replies + interested</option><option value="reply">All replies</option><option value="interested">Interested only</option></select></div>
              </div>
              <label class="fl">Endpoint URL</label>
              <input type="url" v-model="hook.url" required placeholder="https://hooks.slack.com/services/…">
              <button class="btn btn-g btn-sm" type="submit" style="margin-top:12px"><i v-ic="'plus'"></i> Add webhook</button>
            </form>
            <div style="margin-top:16px">
              <div v-for="w in hooks" :key="w.id" class="note">
                <div class="note-meta"><span class="note-cat">{{w.name}}</span><span class="note-time">{{w.events}}</span></div>
                <div class="mono dim" style="font-size:.68rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{w.url}}</div>
              </div>
              <div v-if="!hooks.length" class="empty" style="padding:18px"><div class="empty-d">No webhooks configured.</div></div>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="panel"><div class="empty"><div class="empty-ic"><i v-ic="'eye'"></i></div><div class="empty-t">Page not found</div><div class="empty-d"><a href="#/" style="color:var(--acc)">← Back to dashboard</a></div></div></div>
      </template>

    </main>
  </div>

  <lead-modal v-if="modal&&modal.type==='lead'" :lead="modal.data" @close="close()" @toast="(m,e)=>toast(m,e)" />
  <camp-modal v-if="modal&&modal.type==='campdetail'" :camp="modal.data" @close="close()" @toast="(m,e)=>toast(m,e)" />
  <import-modal v-if="modal&&modal.type==='import'" @close="close()" @done="afterImportDone" @toast="(m,e)=>toast(m,e)" />
  <new-camp-modal v-if="modal&&modal.type==='newcamp'" @close="close()" @done="loadCampaigns(true)" @toast="(m,e)=>toast(m,e)" />

  <div class="toasts">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="{err:t.err}">
      <i v-ic="t.err?'alert-triangle':'check'"></i>{{t.msg}}
    </div>
  </div>
</div>

<div class="auth-wrap" v-else>
  <div class="auth-brand">
    <div class="auth-brand-inner">
      <div class="brand-mark big"><i v-ic="'radar'"></i></div>
      <h1>${BRAND.name}</h1>
      <p>${BRAND.tagline}</p>
    </div>
  </div>
  <div class="auth-side">
    <form class="auth-card" @submit.prevent="authMode==='login'?doLogin():authMode==='forgot'?doForgot():doReset()">
      <template v-if="authMode==='login'">
        <h2>Sign in</h2>
        <p class="hint" style="margin-top:2px">Use your admin email to access the dashboard.</p>
        <label class="fl">Email</label>
        <input type="email" v-model="authForm.email" required placeholder="you@company.com" autofocus>
        <label class="fl">Password</label>
        <input type="password" v-model="authForm.password" required placeholder="••••••••">
        <button class="btn btn-p auth-btn" type="submit" :disabled="authBusy">{{authBusy?'Signing in…':'Sign in'}}</button>
        <button type="button" class="auth-link" @click="authMode='forgot';authErr='';authMsg=''">Forgot password?</button>
      </template>
      <template v-else-if="authMode==='forgot'">
        <h2>Recover access</h2>
        <p class="hint" style="margin-top:2px">We drop a 6-digit code straight into your Gmail drafts. Nothing is emailed to anyone else.</p>
        <label class="fl">Account email</label>
        <input type="email" v-model="authForm.email" required placeholder="you@company.com">
        <button class="btn btn-p auth-btn" type="submit" :disabled="authBusy">{{authBusy?'Creating draft…':'Send recovery code to drafts'}}</button>
        <button type="button" class="auth-link" @click="authMode='login';authErr='';authMsg=''">← Back to sign in</button>
      </template>
      <template v-else>
        <h2>Enter recovery code</h2>
        <p class="hint" style="margin-top:2px">{{authMsg||'Check your Gmail drafts for the code.'}}</p>
        <label class="fl">6-digit code</label>
        <input class="otp-input" type="text" v-model="authForm.otp" required maxlength="6" inputmode="numeric" pattern="[0-9]*" placeholder="••••••">
        <label class="fl">New password</label>
        <input type="password" v-model="authForm.newPassword" required minlength="6" placeholder="At least 6 characters">
        <button class="btn btn-p auth-btn" type="submit" :disabled="authBusy">{{authBusy?'Updating…':'Set new password'}}</button>
        <button type="button" class="auth-link" @click="authMode='login';authErr='';authMsg=''">← Back to sign in</button>
      </template>
      <p v-if="authErr" class="auth-err"><i v-ic="'alert-triangle'"></i>{{authErr}}</p>
    </form>
  </div>
</div>`,
};

const app = Vue.createApp(App);
app.directive('ic', {
  mounted(el, b) { renderIcon(el, b.value); },
  updated(el, b) { if (el._i !== b.value) renderIcon(el, b.value); },
});

app.component('modal-shell', {
  props: { wide: Boolean },
  emits: ['close'],
  template: `
  <div class="mask" @mousedown.self="$emit('close')">
    <div class="modal" :class="{wide}">
      <div class="modal-h">
        <div class="modal-t"><slot name="t"></slot></div>
        <button class="icon-btn" @click="$emit('close')"><i v-ic="'x'"></i></button>
      </div>
      <div class="modal-b"><slot></slot></div>
    </div>
  </div>`,
});

app.component('lead-modal', {
  props: ['lead'],
  emits: ['close', 'toast'],
  data() { return { tab: 'overview', notes: [], activity: [], threads: [], noteText: '', noteCat: 'general', saving: false, ver: null, verifying: false }; },
  template: `
  <modal-shell wide @close="$emit('close')">
    <template #t><i v-ic="'user'"></i> {{lead.name}}</template>
    <div class="tabs">
      <button class="tab" :class="{on:tab==='overview'}" @click="tab='overview'">Overview</button>
      <button class="tab" :class="{on:tab==='notes'}" @click="tab='notes';loadNotes()">Notes</button>
      <button class="tab" :class="{on:tab==='activity'}" @click="tab='activity';loadActivity()">Activity</button>
      <button class="tab" :class="{on:tab==='thread'}" @click="tab='thread';loadThreads()">Thread</button>
      <button class="tab" :class="{on:tab==='check'}" @click="tab='check'">Verify</button>
    </div>
    <div v-if="tab==='overview'" style="padding-top:16px">
      <div class="meta-grid">
        <div><div class="meta-l">Email</div><div class="meta-v copy-row"><span class="mono">{{lead.email||'—'}}</span><button class="icon-btn" v-if="lead.email" @click="cp(lead.email)"><i v-ic="'copy'"></i></button></div></div>
        <div><div class="meta-l">Phone</div><div class="meta-v copy-row"><span class="mono">{{lead.phone||'—'}}</span><button class="icon-btn" v-if="lead.phone" @click="cp(lead.phone)"><i v-ic="'copy'"></i></button></div></div>
        <div><div class="meta-l">Status</div><div class="meta-v"><span class="bg" :class="'bg-'+lead.status">{{lead.status}}</span></div></div>
        <div><div class="meta-l">Priority</div><div class="meta-v"><span class="bg" :class="'bg-'+(lead.priority||'low')">{{lead.priority||'low'}}</span></div></div>
        <div><div class="meta-l">Website</div><div class="meta-v mono" style="font-size:.78rem">{{lead.website_url||'—'}}</div></div>
        <div><div class="meta-l">Event</div><div class="meta-v">{{lead.event_name||'—'}}</div></div>
      </div>
      <label class="fl">Pipeline stage</label>
      <select :value="lead.pipeline_stage||'new'" @change="stage($event)">
        <option v-for="s in $root.stages" :key="s" :value="s">{{s}}</option>
      </select>
      <div v-if="lead.ai_summary" style="margin-top:14px">
        <div class="meta-l">AI summary</div>
        <p class="mut" style="font-size:.82rem;margin-top:4px">{{lead.ai_summary}}</p>
      </div>
      <div style="display:flex;gap:8px;margin-top:18px;padding-top:14px;border-top:1px solid var(--line)">
        <button class="btn btn-g btn-sm" @click="enrich" :disabled="saving"><i v-ic="'sparkles'"></i> {{saving?'Enriching…':'Enrich from web'}}</button>
        <a v-if="lead.website_url" class="btn btn-o btn-sm" :href="lead.website_url" target="_blank" rel="noopener"><i v-ic="'external-link'"></i> Site</a>
      </div>
    </div>
    <div v-if="tab==='notes'" style="padding-top:16px">
      <form @submit.prevent="addNote">
        <textarea v-model="noteText" rows="3" placeholder="Add a note…"></textarea>
        <div style="display:flex;gap:8px;margin-top:8px">
          <select v-model="noteCat" style="max-width:160px"><option>general</option><option>call</option><option>meeting</option><option>follow_up</option><option>deal</option></select>
          <button class="btn btn-p btn-sm" type="submit"><i v-ic="'plus'"></i> Add note</button>
        </div>
      </form>
      <div style="margin-top:16px">
        <div v-for="n in notes" :key="n.id" class="note">
          <div class="note-meta"><span class="note-cat">{{n.category}}</span><span class="note-time">{{ago(n.created_at)}}</span></div>
          <div style="font-size:.82rem;white-space:pre-wrap">{{n.note}}</div>
        </div>
        <div v-if="!notes.length" class="empty" style="padding:22px"><div class="empty-d">No notes yet.</div></div>
      </div>
    </div>
    <div v-if="tab==='activity'" style="padding-top:16px">
      <div v-for="(a,i) in activity" :key="i" class="act-row">
        <span class="act-dot"></span>
        <div style="flex:1;min-width:0"><strong>{{a.action}}</strong> <span class="mut" v-if="a.detail">— {{a.detail}}</span></div>
        <span class="act-time">{{ago(a.created_at)}}</span>
      </div>
      <div v-if="!activity.length" class="empty" style="padding:22px"><div class="empty-d">No activity recorded.</div></div>
    </div>
    <div v-if="tab==='thread'" style="padding-top:16px">
      <div v-for="t in threads" :key="t.id" class="note">
        <div class="note-meta"><span class="note-cat">{{t.subject||'(no subject)'}}</span><span class="note-time">{{ago(t.last_message_at)}}</span></div>
        <div class="mono dim" style="font-size:.68rem">{{t.message_count||1}} messages</div>
      </div>
      <div v-if="!threads.length" class="empty" style="padding:22px"><div class="empty-d">No email thread yet.</div></div>
    </div>
    <div v-if="tab==='check'" style="padding-top:16px">
      <button class="btn btn-p btn-sm" @click="verify" :disabled="verifying||!lead.email"><i v-ic="'shield-check'"></i> {{verifying?'Checking…':'Verify deliverability'}}</button>
      <div v-if="ver" class="meta-grid" style="margin-top:16px">
        <div><div class="meta-l">Status</div><div class="meta-v"><span class="bg" :class="'bg-'+ver.status">{{ver.status}}</span></div></div>
        <div><div class="meta-l">MX records</div><div class="meta-v">{{ver.mx_valid?'Valid':'Missing'}}</div></div>
        <div><div class="meta-l">Disposable</div><div class="meta-v">{{ver.disposable?'Yes':'No'}}</div></div>
        <div><div class="meta-l">Provider</div><div class="meta-v">{{ver.free_provider?'Free mail':'Business'}}</div></div>
        <div><div class="meta-l">Role account</div><div class="meta-v">{{ver.role_account?'Yes':'No'}}</div></div>
      </div>
      <p class="hint" style="margin-top:12px">Checks DNS MX records, disposable domains, known free providers and role inboxes before you send.</p>
    </div>
  </modal-shell>`,
  methods: {
    ago: timeago,
    cp(t) { copyTxt(t); this.$emit('toast', 'Copied'); },
    async loadNotes() { try { this.notes = (await GET('/api/leads/' + this.lead.id + '/notes')).notes || []; } catch (e) {} },
    async loadActivity() { try { this.activity = (await GET('/api/leads/' + this.lead.id + '/activity')).activity || []; } catch (e) {} },
    async loadThreads() { try { this.threads = (await GET('/api/threads?lead_id=' + this.lead.id)).threads || []; } catch (e) {} },
    async addNote() {
      if (!this.noteText.trim()) return;
      const fd = new FormData(); fd.append('note', this.noteText); fd.append('category', this.noteCat);
      try { await POSTF('/api/leads/' + this.lead.id + '/notes', fd); this.noteText = ''; await this.loadNotes(); this.$emit('toast', 'Note added'); }
      catch (e) { this.$emit('toast', e.message, true); }
    },
    async stage(e) {
      const fd = new FormData(); fd.append('stage', e.target.value);
      try {
        await POSTF('/api/leads/' + this.lead.id + '/stage', fd);
        this.lead.pipeline_stage = e.target.value;
        this.$emit('toast', 'Moved to ' + e.target.value);
      } catch (err) { this.$emit('toast', err.message, true); }
    },
    async verify() {
      this.verifying = true;
      try {
        const fd = new FormData(); fd.append('email', this.lead.email);
        this.ver = await fetch(CFG.api + '/api/verify-email', { method: 'POST', body: fd }).then(x => x.json());
        this.$emit('toast', 'Verification: ' + this.ver.status);
      } catch (e) { this.$emit('toast', e.message, true); }
      this.verifying = false;
    },
    async enrich() {
      this.saving = true;
      try {
        await POSTF('/api/leads/' + this.lead.id + '/enrich', new FormData());
        const fresh = (await GET('/api/leads?limit=1000')).leads.find(l => l.id === this.lead.id);
        if (fresh) { this.lead.website_url = fresh.website_url; this.lead.social_url = fresh.social_url; this.lead.city = fresh.city; }
        this.$emit('toast', 'Lead enriched');
      } catch (e) { this.$emit('toast', e.message, true); }
      this.saving = false;
    },
  },
});

app.component('camp-modal', {
  props: ['camp'],
  emits: ['close', 'toast'],
  data() { return { tab: 'steps', steps: [], variants: [], vn: '', vs: '', vb: '' }; },
  mounted() { this.loadSteps(); },
  template: `
  <modal-shell wide @close="$emit('close')">
    <template #t><i v-ic="'radar'"></i> {{camp.name}}</template>
    <div class="tabs">
      <button class="tab" :class="{on:tab==='steps'}" @click="tab='steps'">Sequence</button>
      <button class="tab" :class="{on:tab==='ab'}" @click="tab='ab';loadVariants()">A/B tests</button>
      <button class="tab" :class="{on:tab==='body'}" @click="tab='body'">Initial email</button>
    </div>
    <div v-if="tab==='steps'" style="padding-top:16px">
      <div v-for="s in steps" :key="s.id" class="note">
        <div class="note-meta"><span class="note-cat">Step {{s.step_order}}</span><span class="note-time">day {{s.delay_days}}</span></div>
        <div style="font-size:.76rem;white-space:pre-wrap;font-family:var(--mono)">{{trunc(s.body_template,260)}}</div>
      </div>
      <div v-if="!steps.length" class="empty" style="padding:22px"><div class="empty-d">No follow-up steps — only the initial email sends.</div></div>
    </div>
    <div v-if="tab==='ab'" style="padding-top:16px">
      <form @submit.prevent="addVariant">
        <label class="fl">Variant name</label>
        <input type="text" v-model="vn" required placeholder="Shorter subject">
        <label class="fl">Subject template</label>
        <input type="text" v-model="vs" placeholder="Quick question about {{ '{{name}}' }}">
        <label class="fl">Body template</label>
        <textarea v-model="vb" rows="4" required placeholder="Alternative body copy…"></textarea>
        <button class="btn btn-g btn-sm" type="submit" style="margin-top:10px"><i v-ic="'plus'"></i> Add variant</button>
      </form>
      <div style="margin-top:14px">
        <div v-for="v in variants" :key="v.id" class="note">
          <div class="note-meta"><span class="note-cat">{{v.variant_name}}</span><span class="note-time">sent {{v.sent_count||0}} · replies {{v.reply_count||0}}</span></div>
        </div>
      </div>
    </div>
    <div v-if="tab==='body'" style="padding-top:16px">
      <div class="meta-l">Subject</div>
      <p style="font-size:.86rem;font-weight:700;margin:4px 0 14px">{{camp.subject_template}}</p>
      <div class="meta-l">Body</div>
      <p style="font-size:.8rem;white-space:pre-wrap;font-family:var(--mono);margin-top:6px;color:var(--mut)">{{camp.body_template}}</p>
    </div>
  </modal-shell>`,
  methods: {
    trunc(s, n) { s = s || ''; return s.length > n ? s.slice(0, n) + '…' : s; },
    ago: timeago,
    async loadSteps() { try { this.steps = (await GET('/api/campaigns/' + this.camp.id + '/steps')).steps || []; } catch (e) {} },
    async loadVariants() { try { this.variants = (await GET('/api/campaigns/' + this.camp.id + '/ab-variants')).variants || []; } catch (e) {} },
    async addVariant() {
      try {
        const fd = new FormData();
        fd.append('variant_name', this.vn); fd.append('subject_template', this.vs); fd.append('body_template', this.vb);
        await POSTF('/api/campaigns/' + this.camp.id + '/ab-variants', fd);
        this.vn = this.vs = this.vb = '';
        await this.loadVariants();
        this.$emit('toast', 'Variant added');
      } catch (e) { this.$emit('toast', e.message, true); }
    },
  },
});

app.component('import-modal', {
  emits: ['close', 'done', 'toast'],
  data() { return { busy: false }; },
  template: `
  <modal-shell @close="$emit('close')">
    <template #t><i v-ic="'download'"></i> Import contacts</template>
    <p class="mut" style="font-size:.82rem;margin-top:10px">Paste a contact sheet or upload a file. Handles TSV, CSV, pipe tables and plain lines.</p>
    <form @submit.prevent="doImport($event)">
      <label class="fl">File</label>
      <input type="file" name="file" accept=".txt,.csv,.md,.tsv">
      <label class="fl">Or paste text</label>
      <textarea name="text" rows="7" placeholder="1&#9;Bigmiitch Events&#9;+254 724 214 461&#9;info@bigmiitchevents.co.ke&#9;high"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button type="button" class="btn btn-o" @click="$emit('close')">Cancel</button>
        <button class="btn btn-p" type="submit" :disabled="busy"><i v-ic="'upload'"></i> {{busy?'Importing…':'Import'}}</button>
      </div>
    </form>
  </modal-shell>`,
  methods: {
    async doImport(ev) {
      this.busy = true;
      try {
        const r = await fetch(CFG.api + '/api/import', { method: 'POST', headers: CFG.token ? { 'X-Dashboard-Token': CFG.token } : {}, body: new FormData(ev.target) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || r.statusText);
        this.$emit('toast', 'Imported ' + (j.imported || 0) + ' contacts (' + (j.duplicates || 0) + ' duplicates)');
        this.$emit('done');
        this.$emit('close');
      } catch (e) { this.$emit('toast', e.message, true); }
      this.busy = false;
    },
  },
});

app.component('new-camp-modal', {
  emits: ['close', 'done', 'toast'],
  data() { return { busy: false, f: { name: '', subject_template: '', body_template: '', fu1d: 3, fu1b: '', fu2d: 7, fu2b: '' } }; },
  template: `
  <modal-shell wide @close="$emit('close')">
    <template #t><i v-ic="'plus'"></i> New campaign</template>
    <form @submit.prevent="save">
      <div class="frow">
        <div><label class="fl">Name</label><input type="text" v-model="f.name" required placeholder="Taptap — December outreach"></div>
        <div><label class="fl">Rate limit</label><input type="text" value="25/day · 5 min gaps" disabled></div>
      </div>
      <label class="fl">Subject line</label>
      <input type="text" v-model="f.subject_template" required placeholder="Taptap for {{ '{{name}}' }} — upcoming events">
      <label class="fl">Body</label>
      <textarea v-model="f.body_template" rows="7" required placeholder="Hi {{ '{{first_name}}' }}, …"></textarea>
      <p class="hint">Variables: <span class="mono">{{ '{{name}} {{first_name}} {{email}} {{event_name}}' }}</span></p>
      <div class="frow" style="margin-top:4px">
        <div><label class="fl">Follow-up 1 · day</label><input type="number" v-model="f.fu1d"></div>
        <div><label class="fl">Follow-up 2 · day</label><input type="number" v-model="f.fu2d"></div>
      </div>
      <label class="fl">Follow-up 1 body</label>
      <textarea v-model="f.fu1b" rows="3"></textarea>
      <label class="fl">Follow-up 2 body</label>
      <textarea v-model="f.fu2b" rows="3"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button type="button" class="btn btn-o" @click="$emit('close')">Cancel</button>
        <button class="btn btn-p" type="submit" :disabled="busy">{{busy?'Creating…':'Create campaign'}}</button>
      </div>
    </form>
  </modal-shell>`,
  methods: {
    async save() {
      this.busy = true;
      try {
        const fd = new FormData();
        fd.append('name', this.f.name);
        fd.append('subject_template', this.f.subject_template);
        fd.append('body_template', this.f.body_template);
        fd.append('follow_up_1_days', this.f.fu1d); fd.append('follow_up_1_body', this.f.fu1b);
        fd.append('follow_up_2_days', this.f.fu2d); fd.append('follow_up_2_body', this.f.fu2b);
        const r = await fetch(CFG.api + '/api/campaigns/create', { method: 'POST', headers: CFG.token ? { 'X-Dashboard-Token': CFG.token } : {}, body: fd, redirect: 'follow' });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        this.$emit('toast', 'Campaign created');
        this.$emit('done');
        this.$emit('close');
      } catch (e) { this.$emit('toast', e.message, true); }
      this.busy = false;
    },
  },
});
app.mount('#app');
