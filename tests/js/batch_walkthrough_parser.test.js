'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { parseWalkthroughTranscript, segmentIntents, parseLineItem } =
  require('../../app/static/js/batch_walkthrough_parser.js');

test('empty transcript returns an empty staged result', () => {
  const d = parseWalkthroughTranscript('   ');
  assert.strictEqual(d.matched, 0);
  assert.strictEqual(d.line_items.length, 0);
  assert.strictEqual(d.assemblies.length, 0);
  assert.strictEqual(d.title, null);
});

test('full walkthrough transcript extracts every entity once', () => {
  const d = parseWalkthroughTranscript(
    'quote name is Garage Addition client name is Brandon his address is 1846.5 A Nuisance Lane ' +
    'add 15 gallons of paint at 18 bucks a piece and 20 sheets drywall');
  assert.strictEqual(d.title, 'Garage Addition');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.site_address, '1846.5 A Nuisance Lane');
  assert.strictEqual(d.line_items.length, 2);
  assert.deepStrictEqual(d.line_items[0], { qty: 15, unit: 'gal', description: 'Paint', unit_cost: 18, type: 'material' });
  assert.strictEqual(d.line_items[1].qty, 20);
  assert.strictEqual(d.line_items[1].description, 'Drywall');
  assert.strictEqual(d.assemblies.length, 0);
  assert.strictEqual(d.matched, 5);
});

test('identical line items aggregate across the walkthrough', () => {
  const d = parseWalkthroughTranscript(
    'line item 15 gallons of paint at 18 bucks a piece line item 10 gallons of paint at 18 bucks a piece');
  assert.strictEqual(d.line_items.length, 1);
  assert.strictEqual(d.line_items[0].qty, 25);
  assert.strictEqual(d.line_items[0].description, 'Paint');
});

test('spoken numbers normalize in staged parse', () => {
  const d = parseWalkthroughTranscript(
    'appliance name is Brandon his address is fourteen hundred mockingbird lane fifteen gallons of paint at eighteen bucks a piece');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.site_address, '1400 Mockingbird Lane');
  assert.deepStrictEqual(d.line_items[0], { qty: 15, unit: 'gal', description: 'Paint', unit_cost: 18, type: 'material' });
});

test('parametric assemblies extract dims', () => {
  const d = parseWalkthroughTranscript('drywall 12 by 14 9 ft ceiling framing ten by twelve');
  assert.strictEqual(d.assemblies.length, 2);
  assert.deepStrictEqual(d.assemblies[0], { trade: 'drywall', length: 12, width: 14, height: 9 });
  assert.deepStrictEqual(d.assemblies[1], { trade: 'framing', length: 10, width: 12, height: 8 });
});

test('conversational statements are never line items', () => {
  for (const t of [
    'this paint is not going to work',
    'line item with okay',
    'i think we need 15 gallons of paint',
  ]) {
    const d = parseWalkthroughTranscript(t);
    assert.strictEqual(d.line_items.length, 0, t);
  }
});

test('a bare "line item" produces no Custom Item row', () => {
  const d = parseWalkthroughTranscript('line item');
  assert.strictEqual(d.line_items.length, 0);
});

test('unmatched meaningful speech lands in notes', () => {
  const d = parseWalkthroughTranscript('remind me to call about the water heater');
  assert.strictEqual(d.matched, 0);
  assert.ok(d.notes.length >= 1);
});

test('segmentIntents splits multi-intent utterances', () => {
  assert.deepStrictEqual(
    segmentIntents('client name is Brandon his address is 1846.5 a nuisance lane'),
    ['client name is Brandon', 'his address is 1846.5 a nuisance lane']);
});

test('parseLineItem still parses a single line', () => {
  assert.deepStrictEqual(parseLineItem('20 sheets drywall'), {
    qty: 20, unit: 'sheet', description: 'Drywall', unit_cost: 0, type: 'material',
  });
});
