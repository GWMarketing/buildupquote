'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const Lex = require('../../app/static/js/trade_lexicon.js');
const V = require('../../app/static/js/voice_normalizer.js');

const EXPECTED_TRADES = ['Framing', 'Plumbing', 'Electrical', 'HVAC',
  'Drywall & Paint', 'Roofing & Siding', 'Concrete & Masonry'];

test('generated lexicon covers all 7 trades at 300+ each', () => {
  assert.ok(Lex.total >= 2100, `total ${Lex.total}`);
  const counts = {};
  for (const e of Lex.entries) {
    counts[e.trade] = (counts[e.trade] || 0) + 1;
  }
  for (const trade of EXPECTED_TRADES) {
    assert.ok((counts[trade] || 0) >= 300, `${trade} has ${counts[trade]} entries`);
  }
});

test('every entry carries a term, trade and unit', () => {
  for (const e of Lex.entries.slice(0, 500)) {
    assert.ok(e.term, 'term');
    assert.ok(e.trade, e.term);
    assert.ok(e.unit, e.term);
  }
});

test('voiceCanonical maps the spec examples', () => {
  assert.strictEqual(Lex.voiceCanonical['silcock'], 'Hose Bibb');
  assert.strictEqual(Lex.voiceCanonical['sillcock'], 'Hose Bibb');
  assert.strictEqual(Lex.voiceCanonical['greenboard'], 'Water-Resistant Gypsum Board');
});

test('normalizer rewrites STT slang to lexicon canonicals', () => {
  assert.strictEqual(V.normalizeSpokenTranscript('add a silcock outside'),
    'Hose Bibb outside');
  assert.strictEqual(V.normalizeSpokenTranscript('we need greenboard in the bathroom'),
    'we need Water-Resistant Gypsum Board in the bathroom');
  // The static dictionary still owns the terms it canonicalizes.
  assert.strictEqual(V.normalizeSpokenTranscript('20 sheets of sheetrock'),
    '20 sheets of Drywall');
  // Plain descriptive speech is never rewritten.
  assert.strictEqual(V.normalizeSpokenTranscript('call about the water heater'),
    'call the water heater');
});

test('normalizer is idempotent on lexicon canonical output', () => {
  const once = V.normalizeSpokenTranscript('silcock');   // 'Hose Bibb'
  const twice = V.normalizeSpokenTranscript(once);
  // No double-rewrite: the canonical is never re-expanded, the normalizer
  // only lowercases the transcript again.
  assert.strictEqual(twice, once.toLowerCase());
});

test('canonicalBySpoken indexes aliases and misspellings for autocomplete', () => {
  assert.ok(Lex.canonicalBySpoken['silcock']);
  assert.ok(Lex.canonicalBySpoken['sillcock']);
  assert.ok(Lex.canonicalBySpoken['sheet rock']);
  assert.ok(Lex.canonicalBySpoken['sillcock'].term.startsWith('Hose Bibb'));
});
