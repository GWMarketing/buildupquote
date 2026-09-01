'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const { BQ } = require('../../app/static/js/app.js');

const rows = [
  { 'Include': true, 'Trade': 'Roofing', 'Material': true, 'Qty': 10, 'Unit Cost': 20, 'Margin %': 20 },
  { 'Include': true, 'Trade': 'Roofing', 'Material': false, 'Qty': 5, 'Unit Cost': 50, 'Margin %': 10 },
  { 'Include': true, 'Trade': 'Drywall', 'Material': true, 'Qty': 2, 'Unit Cost': 100, 'Margin %': 0 },
];
function subtotalOf(rows) {
  return rows.filter(r => r['Include'] !== false)
    .reduce((s, r) => s + BQ.round2(Number(r['Qty'] || 0) * Number(r['Unit Cost'] || 0) * (1 + Number(r['Margin %'] || 0) / 100)), 0);
}

function materialSubtotalOf(rows) {
  return rows.filter(r => r['Include'] !== false && r['Material'] !== false)
    .reduce((s, r) => s + BQ.round2(Number(r['Qty'] || 0) * Number(r['Unit Cost'] || 0) * (1 + Number(r['Margin %'] || 0) / 100)), 0);
}

test('no-tax rule returns the plain subtotal', () => {
  const t = BQ.computeTotals(rows, 'none', 8.25);
  assert.strictEqual(t.tax, 0);
  assert.strictEqual(t.subtotal, BQ.round2(subtotalOf(rows)));
  assert.strictEqual(t.total, t.subtotal);
  assert.strictEqual(t.count, 3);
});

test('separated-residential taxes materials only', () => {
  const t = BQ.computeTotals(rows, 'separated_residential', 8.25);
  const mat = BQ.round2(materialSubtotalOf(rows));
  assert.strictEqual(t.tax, BQ.round2(mat * 0.0825));
  assert.strictEqual(t.total, BQ.round2(t.subtotal + t.tax));
});

test('commercial taxes the whole subtotal', () => {
  const t = BQ.computeTotals(rows, 'commercial', 8.25);
  assert.strictEqual(t.tax, BQ.round2(BQ.round2(subtotalOf(rows)) * 0.0825));
});

test('unchecking a line drops it from every total', () => {
  const fewer = rows.map(r => ({ ...r }));
  fewer[0]['Include'] = false;
  const t = BQ.computeTotals(fewer, 'separated_residential', 8.25);
  assert.strictEqual(t.count, 2);
  assert.strictEqual(t.subtotal, BQ.round2(subtotalOf(fewer)));
  assert.strictEqual(t.tax, BQ.round2(BQ.round2(materialSubtotalOf(fewer)) * 0.0825));
});

test('edits to qty/cost/margin change the live totals immediately', () => {
  const edited = rows.map(r => ({ ...r }));
  edited[0]['Qty'] = 25;
  const before = BQ.computeTotals(rows, 'none', 0);
  const after = BQ.computeTotals(edited, 'none', 0);
  assert.ok(after.subtotal > before.subtotal);
  assert.strictEqual(after.subtotal, BQ.round2(subtotalOf(edited)));
});

test('tradeTotals groups included rows by trade, highest first', () => {
  const by = BQ.tradeTotals(rows);
  assert.deepStrictEqual(by.map(g => g.Trade), ['Roofing', 'Drywall']);
  assert.ok(by[0].Subtotal >= by[1].Subtotal);
});

test('matches the reference Python scenario (server parity)', () => {
  const ref = [
    { 'Include': true, 'Material': true, 'Qty': 12.5, 'Unit Cost': 4.99, 'Margin %': 20 },
    { 'Include': false, 'Material': true, 'Qty': 100, 'Unit Cost': 10, 'Margin %': 0 },
    { 'Include': true, 'Material': false, 'Qty': 3, 'Unit Cost': 60, 'Margin %': 15 },
  ];
  const t = BQ.computeTotals(ref, 'separated_residential', 8.25);
  assert.strictEqual(t.subtotal, 74.85 + BQ.round2(3 * 60 * 1.15));
  assert.strictEqual(t.tax, BQ.round2(74.85 * 0.0825));
  assert.strictEqual(t.count, 2);
});
