'use strict';

const test = require('node:test');
const assert = require('node:assert');

const V = require('../../app/static/js/smart_voice_parser.js');

function captureHandlers(overrides) {
  const calls = { stopMic: 0, fields: {}, lineItems: [], notes: [], focused: [], notifies: [] };
  return Object.assign({
    stopMic() { calls.stopMic++; },
    notify(msg) { calls.notifies.push(msg); },
    setFieldValue(field, val) { calls.fields[field] = val; },
    addLineItem(item) { calls.lineItems.push(item); },
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
});

test('client name detection', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('the client name is John Doe', h);
  assert.strictEqual(r.action, 'set_field');
  assert.strictEqual(h._calls.fields.client_name, 'John Doe');

  const r2 = V.processConversationalVoice('customer is Bob Smith', captureHandlers());
  assert.strictEqual(r2.value, 'Bob Smith');
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
  assert.strictEqual(item.description, 'Half inch drywall');
  assert.strictEqual(item.unit_cost, 16);
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

test('mic off stops the engine', () => {
  const h = captureHandlers();
  const r = V.processConversationalVoice('turn off mic', h);
  assert.strictEqual(r.action, 'mic_off');
  assert.strictEqual(h._calls.stopMic, 1);
  assert.ok(h._calls.notifies.some(n => n.includes('Mic turned off')));
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
