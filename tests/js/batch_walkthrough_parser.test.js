'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { parseWalkthroughTranscript, segmentIntents, parseLineItem } =
  require('../../app/static/js/batch_walkthrough_parser.js');

// The parser buckets every item under a room label; these helpers flatten.
const allLines = d => Object.values(d.rooms).flatMap(r => r.line_items);
const allAsms = d => Object.values(d.rooms).flatMap(r => r.assemblies);

test('empty transcript returns an empty staged result', () => {
  const d = parseWalkthroughTranscript('   ');
  assert.strictEqual(d.matched, 0);
  assert.deepStrictEqual(d.rooms, {});
  assert.strictEqual(d.quote_title, null);
});

test('full walkthrough transcript extracts every entity once', () => {
  const d = parseWalkthroughTranscript(
    'quote name is Garage Addition client name is Brandon his address is 1846.5 A Nuisance Lane ' +
    'add 15 gallons of paint at 18 bucks a piece and 20 sheets drywall');
  assert.strictEqual(d.quote_title, 'Garage Addition');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.site_address, '1846.5 A Nuisance Lane');
  assert.deepStrictEqual(d.rooms['General Area'].line_items[0], {
    id: 'line-1', checked: true, qty: 15, unit: 'gal', description: 'Paint',
    unit_cost: 18, type: 'material', trade: 'paint',
  });
  assert.strictEqual(d.rooms['General Area'].line_items[1].qty, 20);
  assert.strictEqual(d.rooms['General Area'].line_items[1].description, 'Drywall');
  assert.strictEqual(allAsms(d).length, 0);
  assert.strictEqual(d.matched, 5);
});

test('identical line items aggregate within a room', () => {
  const d = parseWalkthroughTranscript(
    'line item 15 gallons of paint at 18 bucks a piece line item 10 gallons of paint at 18 bucks a piece');
  assert.strictEqual(allLines(d).length, 1);
  assert.strictEqual(allLines(d)[0].qty, 25);
  assert.strictEqual(allLines(d)[0].description, 'Paint');
});

test('spoken numbers normalize in staged parse', () => {
  const d = parseWalkthroughTranscript(
    'appliance name is Brandon his address is fourteen hundred mockingbird lane fifteen gallons of paint at eighteen bucks a piece');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.site_address, '1400 Mockingbird Lane');
  assert.deepStrictEqual(d.rooms['General Area'].line_items[0], {
    id: 'line-1', checked: true, qty: 15, unit: 'gal', description: 'Paint',
    unit_cost: 18, type: 'material', trade: 'paint',
  });
});

test('parametric assemblies extract dims', () => {
  const d = parseWalkthroughTranscript('drywall 12 by 14 9 ft ceiling framing ten by twelve');
  assert.strictEqual(allAsms(d).length, 2);
  assert.deepStrictEqual(d.rooms['General Area'].assemblies[0], {
    id: 'asm-1', checked: true, trade: 'drywall', trade_key: 'drywall',
    length: 12, width: 14, height: 9,
  });
  assert.deepStrictEqual(d.rooms['General Area'].assemblies[1], {
    id: 'asm-2', checked: true, trade: 'framing', trade_key: 'framing',
    length: 10, width: 12, height: 8,
  });
});

test('corrections retract the last item and skip the sentence', () => {
  const d = parseWalkthroughTranscript(
    '15 gallons of paint at 18 bucks a piece. scratch that. 20 sheets drywall');
  assert.strictEqual(allLines(d).length, 1);
  assert.strictEqual(allLines(d)[0].description, 'Drywall');
  assert.strictEqual(allLines(d)[0].qty, 20);
  assert.ok(d.skipped_items.some(s => /scratch that/.test(s.originalText)));
  assert.ok(d.skipped_items.every(s => /correction phrase/.test(s.reason)));

  const a = parseWalkthroughTranscript('drywall 12 by 14. cancel that. tile 10 by 12');
  assert.strictEqual(allAsms(a).length, 1);
  assert.strictEqual(allAsms(a)[0].trade, 'tile');
});

test('"nevermind" and "don\'t add" skip without creating anything', () => {
  const d = parseWalkthroughTranscript('nevermind. don\'t add the blue paint');
  assert.strictEqual(allLines(d).length, 0);
  assert.strictEqual(d.matched, 0);
  assert.strictEqual(d.skipped_items.length, 2);
});

test('sentences are split on punctuation but one sentence still parses', () => {
  const d = parseWalkthroughTranscript(
    'client name is Brandon. his address is 1846.5 a nuisance lane. 15 gallons of paint.');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.site_address, '1846.5 A Nuisance Lane');
  assert.strictEqual(allLines(d).length, 1);
});

test('conversational statements are never line items', () => {
  for (const t of [
    'this paint is not going to work',
    'line item with okay',
    'i think we need 15 gallons of paint',
  ]) {
    const d = parseWalkthroughTranscript(t);
    assert.strictEqual(allLines(d).length, 0, t);
  }
});

test('a bare "line item" produces no Custom Item row', () => {
  const d = parseWalkthroughTranscript('line item');
  assert.strictEqual(allLines(d).length, 0);
});

test('unmatched meaningful speech lands in client scope notes', () => {
  const d = parseWalkthroughTranscript('remind me to call about the water heater');
  assert.strictEqual(d.matched, 0);
  assert.ok(d.client_scope_notes.length >= 1);
});

test('unresolved construction phrases land in skipped_items with a reason', () => {
  const d = parseWalkthroughTranscript('we need some gallons');
  assert.strictEqual(allLines(d).length, 0);
  assert.ok(d.skipped_items.some(s => /some gallons/.test(s.originalText) &&
    /Could not resolve/.test(s.reason)));
});

test('short unmatched fragments land in skipped_items', () => {
  const d = parseWalkthroughTranscript('the blue paint');
  assert.strictEqual(allLines(d).length, 0);
  assert.ok(d.skipped_items.some(s => /blue paint/.test(s.originalText) &&
    /Fragment lacked/.test(s.reason)));
});

test('segmentIntents splits multi-intent utterances', () => {
  assert.deepStrictEqual(
    segmentIntents('client name is Brandon his address is 1846.5 a nuisance lane'),
    ['client name is Brandon', 'his address is 1846.5 a nuisance lane']);
});

test('parseLineItem still parses a single line', () => {
  assert.deepStrictEqual(parseLineItem('20 sheets drywall'), {
    qty: 20, unit: 'sheet', description: 'Drywall', unit_cost: 0, type: 'material',
    trade: 'drywall',
  });
});

test('items are grouped by room context', () => {
  const d = parseWalkthroughTranscript(
    'in the kitchen add 15 gallons of paint. in the garage add 10 gallons of paint');
  assert.strictEqual(d.rooms['Kitchen'].line_items[0].qty, 15);
  assert.strictEqual(d.rooms['Kitchen'].line_items[0].description, 'Paint');
  assert.strictEqual(d.rooms['Garage'].line_items[0].qty, 10);
  assert.strictEqual(d.rooms['Garage'].line_items[0].description, 'Paint');
  // Same material, different room -> separate buckets, no aggregation.
  assert.strictEqual(allLines(d).length, 2);
});

test('room phrases are stripped and re-scope assemblies', () => {
  const d = parseWalkthroughTranscript(
    'in the master bedroom 20 sheets drywall. in the bathroom drywall 10 by 12');
  assert.strictEqual(d.rooms['Master Bedroom'].line_items[0].description, 'Drywall');
  assert.strictEqual(d.rooms['Bathroom'].assemblies[0].trade_key, 'drywall');
});

test('numbered rooms and next-room markers set context', () => {
  const d = parseWalkthroughTranscript(
    'next room is the kitchen. bedroom 2 needs 10 gallons of paint at 18 bucks a piece');
  assert.strictEqual(allLines(d).length, 1);
  assert.strictEqual(d.rooms['Bedroom 2'].line_items[0].qty, 10);
  assert.strictEqual(d.rooms['Bedroom 2'].line_items[0].unit_cost, 18);
  assert.strictEqual(d.client_scope_notes.length, 0); // room markers are never notes
});

test('expanded trades parse assemblies and set trade_key', () => {
  const d = parseWalkthroughTranscript(
    'plumbing 10 by 12 electrical 6 by 8 roofing 30 by 40 sheetrock 12 by 14');
  assert.strictEqual(allAsms(d).length, 4);
  assert.deepStrictEqual(
    allAsms(d).map(a => [a.trade, a.trade_key]),
    [['plumbing', 'plumbing'], ['electrical', 'electrical'],
     ['roofing', 'roofing'], ['drywall', 'drywall']]);
});

test('line items guess a trade from the description', () => {
  const d = parseWalkthroughTranscript('add 10 sheets drywall and 5 gallons of paint');
  const lines = allLines(d);
  assert.strictEqual(lines[0].trade, 'drywall');
  assert.strictEqual(lines[1].trade, 'paint');
});

test('client capture stops at the next clause boundary', () => {
  const d = parseWalkthroughTranscript(
    'client name is Brandon next room is the kitchen in the kitchen add 15 gallons of paint');
  assert.strictEqual(d.client_name, 'Brandon');
  assert.strictEqual(d.rooms['Kitchen'].line_items[0].qty, 15);
  assert.strictEqual(d.client_scope_notes.length, 0);
});

test('problems surface no-price lines, and client notes carry the rest', () => {
  const d = parseWalkthroughTranscript(
    '15 gallons of paint. remind me to call about the water heater');
  assert.ok(d.problems.some(p => p.level === 'warn' && /no price/.test(p.text)));
  assert.ok(d.client_scope_notes.some(n => /water heater/.test(n)));
});

test('material aliases canonicalize line descriptions', () => {
  const d = parseWalkthroughTranscript(
    'add 20 sheets of sheetrock. add 3 boxes of gfci outlet. add 2 rolls of 14/2. add 8 recessed lights. add 5 buckets of joint compound');
  const lines = allLines(d);
  assert.deepStrictEqual(lines.map(i => [i.qty, i.unit, i.description, i.trade]), [
    [20, 'sheet', 'Drywall', 'drywall'],
    [3, 'box', 'GFCI Receptacle', 'electrical'],
    [2, 'roll', 'Romex NM-B Wire', 'electrical'],
    [8, 'ea', 'Recessed LED Fixtures', 'electrical'],
    [5, 'bucket', 'Joint Compound', null],
  ]);
});

test('a/an quantity phrases parse with qty 1', () => {
  const d = parseWalkthroughTranscript('a gallon of paint');
  assert.strictEqual(allLines(d).length, 1);
  assert.strictEqual(allLines(d)[0].qty, 1);
  assert.strictEqual(allLines(d)[0].unit, 'gal');
  assert.strictEqual(allLines(d)[0].description, 'Paint');
});

test('compound clauses split on connectors', () => {
  const d = parseWalkthroughTranscript(
    'add 15 gallons of paint, plus 20 sheets drywall; 2 boxes of tile');
  assert.strictEqual(allLines(d).length, 3);
  assert.deepStrictEqual(allLines(d).map(i => i.description), ['Paint', 'Drywall', 'Tile']);
});

test('assembly dims accept a "with" height clause', () => {
  const d = parseWalkthroughTranscript('drywall 12 by 14 with 9 ft ceiling');
  assert.strictEqual(allAsms(d).length, 1);
  assert.strictEqual(allAsms(d)[0].height, 9);
});

test('"project is" sets the quote title', () => {
  const d = parseWalkthroughTranscript('project is garage addition');
  assert.strictEqual(d.quote_title, 'Garage Addition');
});

test('internal crew notes are separated from client scope notes', () => {
  const d = parseWalkthroughTranscript(
    'note foundation crack needs inspection before pouring. client wants a slow close toilet');
  assert.strictEqual(d.internal_crew_notes.length, 1);
  assert.strictEqual(d.client_scope_notes.length, 1);
  assert.ok(/foundation crack/.test(d.internal_crew_notes[0]));
  assert.ok(/slow close toilet/.test(d.client_scope_notes[0]));
  // The "note" prefix is stripped from the internal note.
  assert.ok(!/^note\b/.test(d.internal_crew_notes[0]));
});
