'use strict';

const test = require('node:test');
const assert = require('node:assert');

const V = require('../../app/static/js/smart_voice_parser.js');

function captureHandlers(overrides) {
  const calls = { stopMic: 0, fields: {}, lineItems: [], notes: [], focused: [], notifies: [], insertAssembly: [] };
  return Object.assign({
    stopMic() { calls.stopMic++; },
    notify(msg) { calls.notifies.push(msg); },
    setFieldValue(field, val) { calls.fields[field] = val; },
    addLineItem(item) { calls.lineItems.push(item); },
    insertAssembly(asm) { calls.insertAssembly.push(asm); },
    insertIntoActiveField(text) { calls.focused.push(text); },
    appendSiteNotes(text) { calls.notes.push(text); },
  }, overrides, { _calls: calls });
}

test('cleanFillers strips conversational padding', () => {
  assert.strictEqual(
    V.cleanFillers("the address of the client's home is, you know, 1400 Mockingbird Lane"),
    "the address of the client's home is, 1400 Mockingbird Lane",
  );
  assert.strictEqual(V.cleanFillers('add a new line item 15 gallons of paint roughly $18 a piece'),
    'new line item 15 gallons of paint $18 a piece');
});

test('address detection injects into site_address', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice("the address of the client's home is, you know, 1400 Mockingbird Lane", h);
  assert.strictEqual(r.action, 'set_field');
  assert.strictEqual(r.field, 'site_address');
  assert.strictEqual(h._calls.fields.site_address, '1400 Mockingbird Lane');
  assert.strictEqual(r.value, '1400 Mockingbird Lane');

  const r2 = V.processConversationalVoice('site address is 123 Main St', captureHandlers());
  assert.strictEqual(r2.value, '123 Main St');

  // Spoken numbers normalize to digits (case-preserving).
  const r3 = V.processConversationalVoice(
    'the address of the client\'s home is fourteen hundred Mockingbird Lane', captureHandlers());
  assert.strictEqual(r3.value, '1400 Mockingbird Lane');

  // "his/her" prefix + "should be" connector + title-casing.
  const r5 = V.processConversationalVoice('his address is fourteen hundred mockingbird lane', captureHandlers());
  assert.strictEqual(r5.value, '1400 Mockingbird Lane');
  const r6 = V.processConversationalVoice('site address should be 77 oak ave', captureHandlers());
  assert.strictEqual(r6.value, '77 Oak Ave');

  // Proper nouns with number words survive intact.
  const r4 = V.processConversationalVoice('site address is Half Moon Bay', captureHandlers());
  assert.strictEqual(r4.value, 'Half Moon Bay');
});

test('quote title branch sets the title field', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('quote name should be Master Bath Remodel', h);
  assert.strictEqual(r.action, 'set_field');
  assert.strictEqual(r.field, 'title');
  assert.strictEqual(h._calls.fields.title, 'Master Bath Remodel');

  const r2 = V.processConversationalVoice('call this quote Garage Addition', captureHandlers());
  assert.strictEqual(r2.value, 'Garage Addition');

  const r3 = V.processConversationalVoice('quote title is kitchen reno', captureHandlers());
  assert.strictEqual(r3.value, 'Kitchen Reno');
});

test('quote title stop-word guard skips bare connective titles', () => {
  const h = captureHandlers();
  V.processConversationalVoice('quote name should be the', h);
  assert.strictEqual(h._calls.fields.title, undefined);
});

test('client name detection', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('the client name is John Doe', h);
  assert.strictEqual(r.action, 'set_field');
  assert.strictEqual(h._calls.fields.client_name, 'John Doe');

  const r2 = V.processConversationalVoice('customer is Bob Smith', captureHandlers());
  assert.strictEqual(r2.value, 'Bob Smith');

  // Client names are never run through the lexical normalizer, so number-word
  // names ("Five Points Roofing") keep their exact spelling.
  const h2 = captureHandlers();
  V.processConversationalVoice('the client name is Five Points Roofing', h2);
  assert.strictEqual(h2._calls.fields.client_name, 'Five Points Roofing');

  // Names are title-cased.
  const h3 = captureHandlers();
  V.processConversationalVoice('customer should be john o\'brien', h3);
  assert.strictEqual(h3._calls.fields.client_name, 'John O\'brien');
});

test('line item: gallons with cost', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('add a new line item 15 gallons of paint roughly $18 a piece', h);
  assert.strictEqual(r.action, 'add_line');
  assert.deepStrictEqual(h._calls.lineItems[0], {
    qty: 15, unit: 'gal', description: 'Paint', unit_cost: 18, type: 'material',
  });
});

test('line item: drywall sheets at dollars each', () => {
  const h = captureHandlers();
  V.processConversationalVoice('line item 20 sheets of half inch drywall at 16 dollars each', h);
  const item = h._calls.lineItems[0];
  assert.strictEqual(item.qty, 20);
  assert.strictEqual(item.unit, 'sheet');
  assert.strictEqual(item.description, '1/2" drywall');
  assert.strictEqual(item.unit_cost, 16);
});

test('line item: spoken number words and spoken price', () => {
  const h = captureHandlers();
  V.processConversationalVoice('add fifteen gallons of paint at eighteen bucks a piece', h);
  const item = h._calls.lineItems[0];
  assert.deepStrictEqual(item, {
    qty: 15, unit: 'gal', description: 'Paint', unit_cost: 18, type: 'material',
  });
});

test('line item: compound quantity "two hundred fifty"', () => {
  const h = captureHandlers();
  V.processConversationalVoice('line item two hundred fifty square feet of tile at 4 dollars a square', h);
  const item = h._calls.lineItems[0];
  assert.strictEqual(item.qty, 250);
  assert.strictEqual(item.unit, 'sq ft');
  assert.strictEqual(item.unit_cost, 4);
  assert.strictEqual(item.description, 'Tile');
});

test('line item: studs 2 by 4 at bucks a piece', () => {
  const h = captureHandlers();
  V.processConversationalVoice('10 studs 2 by 4 at 4 bucks a piece', h);
  const item = h._calls.lineItems[0];
  assert.strictEqual(item.qty, 10);
  assert.strictEqual(item.unit, 'stud');
  assert.strictEqual(item.description, '2 by 4');
  assert.strictEqual(item.unit_cost, 4);
});

test('line item: no cost falls back to zero', () => {
  const h = captureHandlers();
  V.processConversationalVoice('line item 100 sheets of drywall', h);
  const item = h._calls.lineItems[0];
  assert.strictEqual(item.qty, 100);
  assert.strictEqual(item.unit_cost, 0);
  assert.strictEqual(item.description, 'Drywall');
});

// ---- Parametric assembly (spec parseVoiceInput branch #4) -------------------

test('assembly with explicit dims routes to insertAssembly', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('drywall 12 by 14 9 ft ceiling', h);
  assert.strictEqual(r.action, 'assembly');
  assert.deepStrictEqual(r.dims, { trade: 'drywall', length: 12, width: 14, height: 9 });
  assert.deepStrictEqual(h._calls.insertAssembly[0], { trade: 'drywall', length: 12, width: 14, height: 9 });
});

test('assembly dims normalize spoken numbers', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('framing ten by twelve nine foot ceilings', h);
  assert.strictEqual(r.action, 'assembly');
  assert.deepStrictEqual(r.dims, { trade: 'framing', length: 10, width: 12, height: 9 });
});

test('assembly with x notation and default height', () => {
  const h = captureHandlers();
  V.processConversationalVoice('paint 12 x 14', h);
  assert.deepStrictEqual(h._calls.insertAssembly[0], { trade: 'paint', length: 12, width: 14, height: 8 });
});

test('keyword-only assembly phrase falls back to matchAssembly', () => {
  const h = captureHandlers({ matchAssembly: (t) => 'DRYWALL' });
  const r = V.processConversationalVoice('drywall partition', h);
  assert.strictEqual(r.action, 'assembly');
  assert.strictEqual(r.code, 'DRYWALL');
  assert.strictEqual(h._calls.insertAssembly.length, 0);
});

test('line items mentioning assembly keywords stay line items', () => {
  const h = captureHandlers({ matchAssembly: (t) => 'PAINT' });
  V.processConversationalVoice('15 gallons of paint at $18 a piece', h);
  assert.strictEqual(h._calls.insertAssembly.length, 0);
  assert.strictEqual(h._calls.lineItems.length, 1);
  assert.strictEqual(h._calls.lineItems[0].description, 'Paint');
});

test('parseVoiceInput is an alias of processConversationalVoice', () => {
  assert.strictEqual(V.parseVoiceInput, V.processConversationalVoice);
});

test('mic off stops the engine', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('turn off mic', h);
  assert.strictEqual(r.action, 'mic_off');
  assert.strictEqual(h._calls.stopMic, 1);
  assert.ok(h._calls.notifies.some(n => n.includes('Mic turned off')));
});

test('"stop mic" also stops the engine', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('stop mic', h);
  assert.strictEqual(r.action, 'mic_off');
  assert.strictEqual(h._calls.stopMic, 1);
});

test('"cancel voice" also stops the engine', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('cancel voice', h);
  assert.strictEqual(r.action, 'mic_off');
  assert.strictEqual(h._calls.stopMic, 1);
});

test('focused field fallback routes speech into the active input', () => {
  const h = captureHandlers({ activeFocusedField: 'line:0:description' });
  const r = V.processConversationalVoice('blue paint primer', h);
  assert.strictEqual(r.action, 'focused');
  assert.deepStrictEqual(h._calls.focused, ['blue paint primer']);
});

test('unmatched speech falls back to notes', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('remind me to call about the water heater', h);
  assert.strictEqual(r.action, 'notes');
  assert.deepStrictEqual(h._calls.notes, ['remind me to call about the water heater']);
});

test('empty transcript is a no-op', () => {
  const r = V.processConversationalVoice('   ', captureHandlers());
  assert.strictEqual(r.action, 'none');
});
