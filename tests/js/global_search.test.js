'use strict';
const { test } = require('node:test');
const assert = require('node:assert');

const { norm, fuzzyScore, buildIndex, searchIndex } = require('../../app/static/js/global_search.js');

test('norm lowercases and collapses whitespace', () => {
  assert.strictEqual(norm('  Roof   Repair  '), 'roof repair');
  assert.strictEqual(norm(null), '');
  assert.strictEqual(norm(undefined), '');
});

test('fuzzyScore returns 0 for no match and scores matches', () => {
  assert.strictEqual(fuzzyScore(['zzz'], 'Roofing Estimate'), 0);
  assert.ok(fuzzyScore(['roof'], 'Roofing Estimate') > 0);
  assert.ok(fuzzyScore(['roof'], 'Roofing Estimate') > fuzzyScore(['oof'], 'Roofing Estimate'));
});

test('fuzzyScore prefixes and word starts rank higher', () => {
  const prefix = fuzzyScore(['roof'], 'Roofing Estimate');
  const mid = fuzzyScore(['roof'], 'Metal Roofing Estimate');
  assert.ok(prefix > mid, 'prefix should beat mid-string match');
  const wordStart = fuzzyScore(['est'], 'Roofing Estimate');
  const mid2 = fuzzyScore(['est'], 'Roofing Fester');
  assert.ok(wordStart > mid2, 'word-start should beat mid-word match');
});

test('fuzzyScore requires every token (AND) and rewards order', () => {
  assert.strictEqual(fuzzyScore(['roof', 'zzz'], 'Roofing Estimate'), 0);
  const inOrder = fuzzyScore(['roof', 'est'], 'Roofing Estimate');
  const reversed = fuzzyScore(['est', 'roof'], 'Roofing Estimate');
  assert.ok(inOrder > reversed, 'in-order token match should score higher');
});

test('buildIndex shapes the four categories', () => {
  const idx = buildIndex(
    [{ id: 1, title: 'Roof replacement', client_name: 'Jane Doe', site_address: '10 Main St', status: 'sent' }],
    [{ id: 7, name: 'Jane Doe', email: 'jane@x.com', phone: '555', site_address: '10 Main St' }],
    [{ id: 3, canonical_name: 'Asphalt shingles', trade: 'Roofing', unit: 'sq ft', default_trade_type: 'Material' }],
    [{ code: 'DRYWALL', name: 'Drywall partition', category: 'Framing', description: '2x4 studs' }],
  );
  assert.strictEqual(idx.quotes[0].href, '/quotes/1');
  assert.strictEqual(idx.quotes[0].meta, 'SENT');
  assert.match(idx.quotes[0].searchText, /Jane Doe/);
  assert.strictEqual(idx.clients[0].href, '/clients');
  assert.strictEqual(idx.catalog[0].href, '/catalog');
  assert.strictEqual(idx.assemblies[0].href, '/quotes/new?assembly=DRYWALL');
});

test('searchIndex groups and orders by score (prefix beats word-start)', () => {
  const mk = (id, title) => ({ id: id, title: title, client_name: 'C', site_address: 'A', status: 'draft' });
  const quotes = [
    mk(1, 'ROOFTOP deck'),     // "roof" prefix -> top
    mk(2, 'Metal Roofing'),    // "roof" word-start
    mk(3, 'Bathroom tile'),    // no match
  ];
  const idx = buildIndex(quotes, [], [], []);
  const res = searchIndex(idx, 'roof', 5);
  assert.deepStrictEqual(res.quotes.map(q => q.title), ['ROOFTOP deck', 'Metal Roofing']);
  assert.strictEqual(res.clients.length, 0);
  assert.strictEqual(res.catalog.length, 0);
});

test('searchIndex caps results per group', () => {
  const quotes = Array.from({ length: 9 }, (_, i) => ({
    id: i + 1, title: 'Roof job ' + (i + 1), client_name: 'C', site_address: 'A', status: 'draft',
  }));
  const res = searchIndex(buildIndex(quotes, [], [], []), 'roof', 3);
  assert.strictEqual(res.quotes.length, 3);
});

test('searchIndex empty query returns empty groups', () => {
  const idx = buildIndex([{ id: 1, title: 'X', client_name: null, site_address: null, status: 'draft' }], [], [], []);
  const res = searchIndex(idx, '   ', 5);
  for (const k of ['quotes', 'clients', 'catalog', 'assemblies']) {
    assert.strictEqual(res[k].length, 0, k);
  }
});

test('searchIndex matches across email/phone/address fields', () => {
  const idx = buildIndex([], [{ id: 9, name: 'Bob', email: 'bob@acme.com', phone: '555-0100', site_address: '88 Oak Ave' }], [], []);
  assert.strictEqual(searchIndex(idx, 'acme', 5).clients.length, 1);
  assert.strictEqual(searchIndex(idx, 'oak ave', 5).clients.length, 1);
  assert.strictEqual(searchIndex(idx, 'bob', 5).clients.length, 1);
  assert.strictEqual(searchIndex(idx, 'nobody', 5).clients.length, 0);
});

test('searchIndex multi-token AND for combined fields', () => {
  const idx = buildIndex(
    [{ id: 2, title: 'Kitchen remodel', client_name: 'Alice', site_address: '4 Park Rd', status: 'accepted' }],
    [], [], [],
  );
  const res = searchIndex(idx, 'kitchen alice', 5);
  assert.strictEqual(res.quotes.length, 1);
  assert.strictEqual(searchIndex(idx, 'kitchen bob', 5).quotes.length, 0);
});
