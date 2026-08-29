/* Universal Global Search / Quick Launcher (Cmd/Ctrl+K).
 *
 * Loaded on every authenticated page via base.html (after app.js / share.js).
 * The pure helpers (norm / fuzzyScore / buildIndex / searchIndex) have no DOM
 * dependencies so they run under Node (tests/js/global_search.test.js);
 * BQSearch wires them to the modal, the keyboard, and a BroadcastChannel that
 * pings open dashboards whenever a client signs a proposal (cross-tab sync).
 *
 * Categories & deep links:
 *   Quotes    -> /quotes/:id          Clients -> /clients
 *   Catalog   -> /catalog             Assemblies -> /quotes/new?assembly=CODE
 */
(function () {
  'use strict';

  /** Lowercase + collapse whitespace for matching. */
  function norm(s) {
    return String(s == null ? '' : s).toLowerCase().replace(/\s+/g, ' ').trim();
  }

  /** Multi-token AND match with relevance scoring. 0 = no match. */
  function fuzzyScore(queryTokens, text) {
    const t = norm(text);
    if (!t) return 0;
    let score = 0;
    let pos = -1;
    for (const token of queryTokens) {
      const idx = t.indexOf(token);
      if (idx === -1) return 0;                    // every token must match
      score += token.length * 10;
      if (idx === 0) score += 40;                  // prefix bonus
      if (pos !== -1 && idx > pos) score += 5;     // in-order bonus
      const boundary = idx === 0 || /[\s(/-]/.test(t[idx - 1] || '');
      if (boundary) score += 25;                   // word-start bonus
      pos = idx;
    }
    return score;
  }

  /** Flatten the API rows into {type,title,searchText,subtitle,href,meta}. */
  function buildIndex(quotes, clients, catalog, assemblies) {
    return {
      quotes: (quotes || []).map(q => ({
        type: 'quote', id: q.id,
        title: q.title || ('Quote #' + q.id),
        searchText: [q.title, q.client_name, q.site_address, q.status, q.id].join(' '),
        subtitle: [q.client_name, q.site_address].filter(Boolean).join(' · ') || 'No client attached',
        href: '/quotes/' + q.id,
        meta: q.status ? String(q.status).toUpperCase() : '',
      })),
      clients: (clients || []).map(c => ({
        type: 'client', id: c.id,
        title: c.name || ('Client #' + c.id),
        searchText: [c.name, c.email, c.phone, c.site_address].join(' '),
        subtitle: [c.phone, c.email].filter(Boolean).join(' · ') || c.site_address || '',
        href: '/clients',
      })),
      catalog: (catalog || []).map(i => ({
        type: 'catalog', id: i.id,
        title: i.canonical_name,
        searchText: [i.canonical_name, i.trade, i.default_trade_type, i.unit].join(' '),
        subtitle: [i.trade, i.unit].filter(Boolean).join(' · '),
        href: '/catalog',
      })),
      assemblies: (assemblies || []).map(a => ({
        type: 'assembly', id: a.code,
        title: a.name || a.code,
        searchText: [a.name, a.code, a.category, a.description].join(' '),
        subtitle: [a.category, a.code].filter(Boolean).join(' · '),
        href: '/quotes/new?assembly=' + encodeURIComponent(a.code),
      })),
    };
  }

  /** Grouped, capped, score-sorted results for a query string. */
  function searchIndex(index, query, perGroup) {
    const tokens = norm(query).split(' ').filter(Boolean);
    const groups = ['quotes', 'clients', 'catalog', 'assemblies'];
    const out = { quotes: [], clients: [], catalog: [], assemblies: [] };
    if (!tokens.length || !index) return out;
    const cap = perGroup || 5;
    for (const group of groups) {
      const scored = [];
      for (const item of index[group]) {
        const s = fuzzyScore(tokens, item.searchText);
        if (s > 0) scored.push({ item: item, score: s });
      }
      scored.sort(function (a, b) {
        return b.score - a.score ||
          String(a.item.title).localeCompare(String(b.item.title));
      });
      out[group] = scored.slice(0, cap).map(function (x) { return x.item; });
    }
    return out;
  }

  const QUICK_ACTIONS = [
    { title: 'Create Quote', href: '/quotes/new', icon: 'file-pen-line' },
    { title: 'Add Client', href: '/clients?new=1', icon: 'user-plus' },
    { title: 'New Invoice', href: '/quotes?view=active', icon: 'receipt-text' },
    { title: 'Import Adjuster PDF', href: '/parser', icon: 'file-text' },
    { title: 'Go to Dashboard', href: '/dashboard', icon: 'layout-dashboard' },
  ];

  function createSearch() {
    var modal = null, input = null, results = null;
    var index = null, indexed = false;
    var flat = [];          // flat list of {href, el} for keyboard nav
    var activeIdx = 0;
    var pendingQuery = '';

    function render() {
      var query = (input.value || '').trim();
      var html = '';
      if (!query) {
        html += '<div class="px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">Quick actions</div>';
        html += '<div class="px-2 pb-2">';
        QUICK_ACTIONS.forEach(function (a) {
          html += '<a href="' + a.href + '" data-bq-act class="group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-slate-300 transition hover:bg-slate-800">' +
            '<i data-lucide="' + a.icon + '" class="h-4 w-4 text-amber-500"></i><span class="flex-1">' + a.title + '</span>' +
            '<i data-lucide="arrow-up-right" class="h-3.5 w-3.5 text-slate-600 group-hover:text-slate-400"></i></a>';
        });
        html += '</div>';
      } else {
        var res = searchIndex(index, query, 5);
        var sections = [
          ['Quotes', 'quotes', 'file-pen-line', 'text-amber-500', function (item) {
            var pill = item.meta
              ? '<span class="rounded-full bg-slate-700 px-2 py-0.5 text-[10px] font-bold text-slate-300">' + item.meta + '</span>'
              : '';
            return item.title + ' ' + pill;
          }],
          ['Clients', 'clients', 'users', 'text-sky-400', null],
          ['Rate Catalog', 'catalog', 'book-open', 'text-emerald-400', null],
          ['Assemblies', 'assemblies', 'blocks', 'text-violet-400', null],
        ];
        var any = false;
        sections.forEach(function (sec) {
          var items = res[sec[1]];
          if (!items || !items.length) return;
          any = true;
          html += '<div class="px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">' + sec[0] + '</div>';
          html += '<div class="px-2 pb-1">';
          items.forEach(function (item) {
            var title = sec[4] ? sec[4](item) : item.title;
            var sub = item.subtitle ? '<div class="text-xs text-slate-500">' + escapeHtml(item.subtitle) + '</div>' : '';
            html += '<a href="' + item.href + '" data-bq-act class="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-slate-800">' +
              '<i data-lucide="' + sec[2] + '" class="h-4 w-4 shrink-0 ' + sec[3] + '"></i>' +
              '<span class="min-w-0 flex-1"><span class="block truncate text-sm font-medium text-slate-200">' + title + '</span>' + sub + '</span>' +
              '<i data-lucide="corner-down-left" class="h-3.5 w-3.5 text-slate-600"></i></a>';
          });
          html += '</div>';
        });
        if (!any) {
          html += '<div class="px-4 py-8 text-center">' +
            '<p class="text-sm text-slate-400">No matches in your workspace.</p>' +
            '<a href="/quotes?q=' + encodeURIComponent(query) + '" data-bq-act class="mt-2 inline-block text-xs font-semibold text-amber-400 hover:text-amber-300">Search all quotes for "' + escapeHtml(query) + '" →</a>' +
            '</div>';
        }
      }
      results.innerHTML = html;
      if (window.lucide) lucide.createIcons();
      flat = Array.prototype.slice.call(results.querySelectorAll('[data-bq-act]'));
      activeIdx = 0;
      markActive();
    }

    function markActive() {
      flat.forEach(function (r, i) {
        r.classList.toggle('bg-slate-800', i === activeIdx);
        if (i === activeIdx) r.scrollIntoView({ block: 'nearest' });
      });
    }

    function open() {
      if (!modal) return;
      modal.classList.remove('hidden');
      if (input) {
        input.value = pendingQuery;
        pendingQuery = '';
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
      document.body.classList.add('overflow-hidden');
      if (!indexed) loadIndex();
      render();
    }

    function close() {
      if (!modal) return;
      modal.classList.add('hidden');
      document.body.classList.remove('overflow-hidden');
    }

    async function loadIndex() {
      indexed = true;
      try {
        const [quotes, clients, catalog, assemblies] = await Promise.all([
          BQ.api('/api/quotes'),
          BQ.api('/api/clients'),
          BQ.api('/api/catalog/items'),
          BQ.api('/api/assemblies'),
        ]);
        index = buildIndex(quotes, clients, catalog, assemblies);
      } catch (e) { /* keep index null; UI falls back to the quick-actions view */ }
      if (!modal.classList.contains('hidden')) render();
    }

    function onKeydown(e) {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        if (modal.classList.contains('hidden')) { open(); } else { close(); }
        return;
      }
      if (modal.classList.contains('hidden')) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = Math.min(activeIdx + 1, Math.max(flat.length - 1, 0));
        markActive();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = Math.max(activeIdx - 1, 0);
        markActive();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        var target = flat[activeIdx];
        if (!target && flat.length) target = flat[0];
        if (target) target.click();
      }
    }

    function initChannel() {
      if (typeof BroadcastChannel === 'undefined') return;
      try {
        channel = new BroadcastChannel('bq_updates');
        channel.onmessage = function (ev) {
          if (ev.data && ev.data.type === 'quote-signed' &&
              typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new CustomEvent('BQ_SHELL_STATS_CHANGED'));
          }
        };
      } catch (e) { /* older browsers: rely on the polling fallback */ }
    }

    return {
      open: open,
      close: close,
      toggle: function () {
        if (modal && modal.classList.contains('hidden')) { open(); } else { close(); }
      },
      init: function () {
        modal = document.getElementById('bq-search-modal');
        if (!modal) return;
        input = document.getElementById('bq-search-input');
        results = document.getElementById('bq-search-results');
        if (!input || !results) return;
        input.addEventListener('input', render);
        modal.querySelectorAll('[data-bq-close]').forEach(function (el) {
          el.addEventListener('click', close);
        });
        document.addEventListener('keydown', onKeydown);
        initChannel();
      },
    };
  }

  var BQSearch = createSearch();
  if (typeof window !== 'undefined') window.BQSearch = BQSearch;
  if (typeof document !== 'undefined' && document.readyState !== 'loading') {
    BQSearch.init();
  } else if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', function () { BQSearch.init(); });
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { norm: norm, fuzzyScore: fuzzyScore, buildIndex: buildIndex, searchIndex: searchIndex };
  }
})();

