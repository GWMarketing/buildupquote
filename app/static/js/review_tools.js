// Review-phase tools for the parser page.
// Column preferences (hide / rename / auto-size) and an undo/redo
// snapshot stack, kept pure so tests/js/review_tools.test.js can lock
// them down like the live-totals math.
const BQReview = {
  DEFAULT_COLUMNS: [
    { key: 'On', label: 'On', kind: 'checkbox' },
    { key: '#', label: '#', kind: 'num' },
    { key: 'Trade', label: 'Trade', kind: 'text' },
    { key: 'Description', label: 'Description', kind: 'text', required: true },
    { key: 'Qty', label: 'Qty', kind: 'num' },
    { key: 'Unit', label: 'Unit', kind: 'text' },
    { key: 'Unit Cost', label: 'Unit Cost', kind: 'num' },
    { key: 'Margin %', label: 'Margin %', kind: 'num' },
    { key: 'Material', label: 'Mat', kind: 'checkbox' },
    { key: 'Your Price', label: 'Your Price', kind: 'num' },
    { key: 'Needs Review', label: 'Review', kind: 'checkbox' },
  ],
  STORAGE_KEY: 'bq.parser.columns.v1',
  defaultColumnConfig() {
    return { groupBySection: true, columns: this.DEFAULT_COLUMNS.map(c => ({ ...c, visible: true })) };
  },
  sanitizeColumnConfig(raw) {
    const out = [];
    const seen = new Set(this.DEFAULT_COLUMNS.map(c => c.key));
    if (raw && Array.isArray(raw.columns)) {
      for (const col of raw.columns) {
        if (!col || !seen.has(col.key)) continue;
        const def = this.DEFAULT_COLUMNS.find(c => c.key === col.key);
        out.push({
          key: col.key,
          label: (typeof col.label === 'string' && col.label.trim()) ? col.label.trim() : def.label,
          kind: def.kind,
          required: !!def.required,
          visible: def.required ? true : col.visible !== false,
          width: Number.isFinite(col.width) ? col.width : null,
        });
        seen.delete(col.key);
      }
    }
    for (const c of this.DEFAULT_COLUMNS) {
      if (seen.has(c.key)) out.push({ ...c, visible: true });
    }
    const groupBySection = !raw || raw.groupBySection !== false;
    return { groupBySection: groupBySection, columns: out };
  },
  load() {
    try {
      if (typeof localStorage === 'undefined') return this.defaultColumnConfig();
      const raw = JSON.parse(localStorage.getItem(this.STORAGE_KEY));
      return this.sanitizeColumnConfig(raw);
    } catch (e) { return this.defaultColumnConfig(); }
  },
  save(config) {
    try {
      if (typeof localStorage === 'undefined') return;
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(config));
    } catch (e) { /* private mode */ }
  },
  visibleColumns(config) { return config.columns.filter(c => c.visible); },
  label(config, key) {
    const c = config.columns.find(x => x.key === key);
    return c ? c.label : key;
  },
  createUndoStack(capacity) {
    let undo = [];
    let redo = [];
    const trim = (arr) => { if (arr.length > capacity) arr.splice(0, arr.length - capacity); };
    return {
      push(state) { undo.push(state); trim(undo); redo = []; },
      undo(after) { if (!undo.length) return null; redo.push(after); trim(redo); return undo.pop(); },
      redo(before) { if (!redo.length) return null; undo.push(before); trim(undo); return redo.pop(); },
      canUndo() { return undo.length > 0; },
      canRedo() { return redo.length > 0; },
      reset() { undo = []; redo = []; },
    };
  },
  // Deep copy minus transient UI keys so undo snapshots never
  // carry half-typed autocomplete state.
  stripRows(rows) {
    return (rows || []).map(r => {
      const out = {};
      for (const k of Object.keys(r)) {
        if (k.charAt(0) === '_') continue;
        out[k] = r[k];
      }
      return out;
    });
  },
  // Sum carrier RCV of INCLUDED rows vs the PDF printed total.
  // Returns { parsed, printed, matches } (null when no printed total).
  reconcile(rows, printedRcv) {
    let parsed = 0;
    for (const r of rows || []) {
      if (r['Include'] === false) continue;
      const v = Number(r['Insurance RCV']);
      if (Number.isFinite(v)) parsed += v;
    }
    const printed = Number(printedRcv);
    const hasPrinted = Number.isFinite(printed) && printed > 0;
    return { parsed: Math.round(parsed * 100) / 100, printed: hasPrinted ? printed : null,
      matches: hasPrinted ? Math.abs(parsed - printed) < 0.01 : null };
  },
};
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { BQReview };
}
