'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { TRADE_SYNONYMS, matchTrade, describeTrade } =
  require('../../app/static/js/trade_catalog.js');

test('trade catalog covers construction, remodeling and MEP trades', () => {
  assert.deepStrictEqual(
    Object.keys(TRADE_SYNONYMS).sort(),
    ['concrete', 'demolition', 'drywall', 'electrical', 'flooring', 'framing',
     'insulation', 'paint', 'plumbing', 'roofing', 'siding', 'tile', 'trim']);
});

test('matchTrade resolves synonyms to the canonical slug', () => {
  assert.strictEqual(matchTrade('drywall'), 'drywall');
  assert.strictEqual(matchTrade('sheetrock'), 'drywall');
  assert.strictEqual(matchTrade('gypsum'), 'drywall');
  assert.strictEqual(matchTrade('mudding'), 'drywall');
  assert.strictEqual(matchTrade('framing'), 'framing');
  assert.strictEqual(matchTrade('studs'), 'framing');
  assert.strictEqual(matchTrade('rough carpentry'), 'framing');
  assert.strictEqual(matchTrade('paint'), 'paint');
  assert.strictEqual(matchTrade('exterior paint'), 'paint');
  assert.strictEqual(matchTrade('lvp'), 'flooring');
  assert.strictEqual(matchTrade('laminate'), 'flooring');
  assert.strictEqual(matchTrade('backsplash'), 'tile');
  assert.strictEqual(matchTrade('porcelain'), 'tile');
  assert.strictEqual(matchTrade('wiring'), 'electrical');
  assert.strictEqual(matchTrade('subpanel'), 'electrical');
  assert.strictEqual(matchTrade('pex'), 'plumbing');
  assert.strictEqual(matchTrade('shower valve'), 'plumbing');
  assert.strictEqual(matchTrade('baseboard'), 'trim');
  assert.strictEqual(matchTrade('crown molding'), 'trim');
  assert.strictEqual(matchTrade('rockwool'), 'insulation');
  assert.strictEqual(matchTrade('shingles'), 'roofing');
  assert.strictEqual(matchTrade('hardie'), 'siding');
  assert.strictEqual(matchTrade('tear out'), 'demolition');
  assert.strictEqual(matchTrade('footing'), 'concrete');
  // Spec ordering: 'flooring' is checked before the dedicated 'tile' key.
  assert.strictEqual(matchTrade('tile'), 'flooring');
  // Case-insensitive + tolerant of extra words / whitespace.
  assert.strictEqual(matchTrade('   SheetRock '), 'drywall');
});

test('matchTrade returns null for unknown or empty input', () => {
  assert.strictEqual(matchTrade(null), null);
  assert.strictEqual(matchTrade(undefined), null);
  assert.strictEqual(matchTrade(''), null);
  assert.strictEqual(matchTrade('   '), null);
  assert.strictEqual(matchTrade('wheelbarrow'), null);
  assert.strictEqual(matchTrade('call about the water heater'), null);
});

test('describeTrade title-cases a canonical slug', () => {
  assert.strictEqual(describeTrade('drywall'), 'Drywall');
  assert.strictEqual(describeTrade('ELECTRICAL'), 'Electrical');
  assert.strictEqual(describeTrade(''), '');
  assert.strictEqual(describeTrade(null), '');
});
