'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { BQReview } = require('../../app/static/js/review_tools.js');

test('default config exposes all canonical columns', () => {
  const cfg = BQReview.defaultColumnConfig();
  assert.strictEqual(cfg.columns.length, BQReview.DEFAULT_COLUMNS.length);
  assert.strictEqual(cfg.groupBySection, true);
  const desc = cfg.columns.find(c => c.key === 'Description');
  assert.strictEqual(desc.required, true);
  assert.strictEqual(desc.visible, true);
});

test('sanitize keeps renames and hides non-required columns', () => {
  const cfg = BQReview.sanitizeColumnConfig({ columns: [
    { key: 'Qty', label: 'Quantity', visible: true },
    { key: 'Margin %', label: 'Markup', visible: false },
  ] });
  assert.strictEqual(BQReview.label(cfg, 'Qty'), 'Quantity');
  assert.strictEqual(BQReview.label(cfg, 'Margin %'), 'Markup');
  assert.strictEqual(cfg.columns.find(c => c.key === 'Margin %').visible, false);
  assert.strictEqual(cfg.columns.find(c => c.key === 'Description').visible, true);
  assert.strictEqual(cfg.columns.find(c => c.key === 'On').visible, true);
});

test('sanitize ignores unknown keys and falls back to defaults', () => {
  const cfg = BQReview.sanitizeColumnConfig({ columns: [{ key: 'Bogus', visible: false }] });
  assert.strictEqual(cfg.columns.length, BQReview.DEFAULT_COLUMNS.length);
  assert.strictEqual(cfg.columns.find(c => c.key === 'Qty').visible, true);
});

test('visibleColumns filters and label falls back to the key', () => {
  const cfg = BQReview.defaultColumnConfig();
  assert.strictEqual(BQReview.visibleColumns(cfg).length, cfg.columns.length);
  assert.strictEqual(BQReview.label(cfg, 'Unknown'), 'Unknown');
});

test('undo/redo restores snapshots and a new edit clears redo', () => {
  const s = BQReview.createUndoStack(10);
  assert.strictEqual(s.undo([2]), null);
  s.push([1]);
  assert.strictEqual(s.canUndo(), true);
  assert.deepStrictEqual(s.undo([2]), [1]);
  assert.strictEqual(s.canRedo(), true);
  assert.deepStrictEqual(s.redo([1]), [2]);
  s.push([2]);
  assert.strictEqual(s.canRedo(), false);
  assert.deepStrictEqual(s.undo([3]), [2]);
  assert.deepStrictEqual(s.redo([2]), [3]);
});

test('undo stack caps at its capacity', () => {
  const s = BQReview.createUndoStack(3);
  for (let i = 0; i < 6; i++) s.push([i]);
  assert.deepStrictEqual(s.undo([99]), [5]);
  assert.deepStrictEqual(s.undo([98]), [4]);
  assert.deepStrictEqual(s.undo([97]), [3]);
  assert.strictEqual(s.canUndo(), false);
});

test('stripRows drops transient underscore keys only', () => {
  const rows = [{ '#': '16', Description: 'Dumpster', _notesOpen: true, _suggestions: [] }];
  const out = BQReview.stripRows(rows);
  assert.strictEqual(out[0].Description, 'Dumpster');
  assert.strictEqual(out[0]._notesOpen, undefined);
  assert.strictEqual(out[0]._suggestions, undefined);
});

test('reconcile sums included RCV and compares to the printed total', () => {
  const rows = [
    { Include: true, 'Insurance RCV': 11443.13 },
    { Include: false, 'Insurance RCV': 712.46 },
    { Include: true, 'Insurance RCV': 328.98 },
  ];
  assert.ok(BQReview.reconcile(rows, 11772.11).matches);
  assert.strictEqual(BQReview.reconcile(rows, 11772.11).parsed, 11772.11);
  assert.strictEqual(BQReview.reconcile(rows, 10000).matches, false);
  assert.strictEqual(BQReview.reconcile(rows, null).printed, null);
});
