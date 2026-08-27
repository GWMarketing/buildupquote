/* BuildUpQuote shared client-side helpers. */
const BQ = {
  token() { return localStorage.getItem('bq_token'); },
  setToken(t) { localStorage.setItem('bq_token', t); },
  clear() { localStorage.removeItem('bq_token'); },

  async api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (this.token()) headers['Authorization'] = 'Bearer ' + this.token();
    const res = await fetch(path, Object.assign({}, opts, { headers }));
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
    return res.json();
  },

  money(n) { return '$' + Number(n || 0).toFixed(2); },

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
