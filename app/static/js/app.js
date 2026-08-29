/* BuildUpQuote shared client-side helpers. */
const BQ = {
  token() { return localStorage.getItem('bq_token'); },
  setToken(t) { localStorage.setItem('bq_token', t); },
  clear() { localStorage.removeItem('bq_token'); },

  /* Standardized API wrapper: attaches the bearer token, auto-serializes
     plain-object bodies as JSON, surfaces server `detail` messages, and on
     401 logs the user out and returns them to /login. */
  async api(path, opts = {}) {
    const res = await this.apiRaw(path, opts);
    return res.json();
  },

  /* Like api() but returns the raw Response (for file downloads / blobs). */
  async apiRaw(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (this.token()) headers['Authorization'] = 'Bearer ' + this.token();
    let body = opts.body;
    if (body && typeof body === 'object' && !(body instanceof FormData)
        && !(body instanceof URLSearchParams) && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    const res = await fetch(path, Object.assign({}, opts, { headers, body }));
    if (res.status === 401 && !path.startsWith('/api/auth/token')) {
      this.clear();
      location.href = '/login';
      throw new Error('unauthorized');
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = (typeof data.detail === 'string') ? data.detail : JSON.stringify(data.detail);
      } catch (e) { /* keep statusText */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res;
  },

  /* Trigger a browser save-as for an attachment response and return the
     server-supplied filename (from Content-Disposition when present). */
  async download(res, fallbackName) {
    let name = fallbackName;
    const cd = res.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="?([^";]+)"?/);
    if (m) name = m[1];
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    return name;
  },

  /* User-visible toast instead of alert()/silent failures. */
  toast(message, type = 'error') {
    let box = document.getElementById('bq-toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'bq-toasts';
      box.className = 'fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2';
      document.body.appendChild(box);
    }
    const el = document.createElement('div');
    el.className = 'rounded-lg px-4 py-3 text-sm font-medium shadow-lg text-white '
      + (type === 'success' ? 'bg-emerald-600' : 'bg-red-600');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  },

  money(n) { return '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },

  statusClass(status) {
    return { draft: 'badge-draft', sent: 'badge-sent', accepted: 'badge-accepted' }[status] || 'badge-draft';
  },

  lineTotal(row) {
    const qty = Number(row.quantity || 0);
    const cost = Number(row.unit_cost || 0);
    const markup = Number(row.markup_percent || 0);
    return qty * cost * (1 + markup / 100);
  },
};

function logout() { BQ.clear(); location.href = '/login'; }

