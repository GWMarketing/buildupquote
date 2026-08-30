'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const V = require('../../app/static/js/voice_normalizer.js');

// ---- Number words -----------------------------------------------------------

test('single number words become digits', () => {
  assert.strictEqual(V.convertNumberWords('fifteen'), '15');
  assert.strictEqual(V.convertNumberWords('eighteen'), '18');
  assert.strictEqual(V.convertNumberWords('six'), '6');
});

test('compound numbers rebuild correctly', () => {
  assert.strictEqual(V.convertNumberWords('twenty five'), '25');
  assert.strictEqual(V.convertNumberWords('twenty-five'), '25');
  assert.strictEqual(V.convertNumberWords('thirty two'), '32');
  assert.strictEqual(V.convertNumberWords('two hundred'), '200');
  assert.strictEqual(V.convertNumberWords('two hundred fifty'), '250');
  assert.strictEqual(V.convertNumberWords('one hundred and five'), '105');
  assert.strictEqual(V.convertNumberWords('a hundred'), '100');
});

test('convertNumberWords is case-insensitive', () => {
  assert.strictEqual(V.convertNumberWords('Fourteen Hundred'), '1400');
  assert.strictEqual(V.convertNumberWords('TWENTY FIVE'), '25');
});

// ---- normalizeSpokenTranscript (line items) ---------------------------------

test('pricing idioms normalize to dollar + /ea', () => {
  assert.strictEqual(V.normalizeSpokenTranscript('18 bucks a piece'), '$18 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('16 dollars each'), '$16 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('4 per unit'), '4 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('30 a gallon'), '30 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('at $18 a piece'), 'at $18 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('18 bucks a box'), '$18 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('2 per bag'), '2 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('30 bucks a roll'), '$30 /ea');
  assert.strictEqual(V.normalizeSpokenTranscript('18 a pop'), '18 /ea');
});

test('material aliases run at the lexical layer', () => {
  assert.strictEqual(V.normalizeSpokenTranscript('20 sheets of sheetrock'), '20 sheets of Drywall');
  assert.strictEqual(V.normalizeSpokenTranscript('add a box of gfci outlet'), 'box of GFCI Receptacle');
  assert.strictEqual(V.normalizeSpokenTranscript('two by four studs'), '2x4 SPF Studs studs');
  assert.strictEqual(V.normalizeSpokenTranscript('8 recessed lights'), '8 Recessed LED Fixtures');
  // Idempotent: normalizing already-canonical text never double-applies.
  // (The pipeline lowercases, so the canonical input round-trips lowercase.)
  assert.strictEqual(V.normalizeSpokenTranscript('8 Recessed LED Fixtures'), '8 recessed led fixtures');
});

test('new planning fillers are stripped', () => {
  const out = V.normalizeSpokenTranscript('we are gonna need 15 gallons of paint');
  assert.strictEqual(out, '15 gallons of paint');
  assert.strictEqual(V.normalizeSpokenTranscript("we'll need 2 boxes of tile"), '2 boxes of tile');
  assert.strictEqual(V.normalizeSpokenTranscript('we will need 3 sheets drywall'), '3 sheets drywall');
});

test('quantity speech like "a gallon of paint" is not destroyed', () => {
  // "add a gallon of paint" -> fillers strip "add a", but "a gallon" is not a
  // price context so it survives as a quantity phrase rather than "/ea".
  const out = V.normalizeSpokenTranscript('add a gallon of paint');
  assert.ok(out.includes('gallon of paint'));
  assert.ok(!out.includes('/ea'));
});

test('dimensional idioms normalize', () => {
  assert.strictEqual(V.normalizeSpokenTranscript('half inch drywall'), '1/2" drywall');
  assert.strictEqual(V.normalizeSpokenTranscript('1/2 inch drywall'), '1/2" drywall');
  assert.strictEqual(V.normalizeSpokenTranscript('five eighths drywall'), '5/8" drywall');
  assert.strictEqual(V.normalizeSpokenTranscript('5/8 inch drywall'), '5/8" drywall');
});

test('full pipeline handles a realistic spoken line item', () => {
  const out = V.normalizeSpokenTranscript(
    'add fifteen gallons of paint at eighteen bucks a piece roughly');
  assert.strictEqual(out, '15 gallons of paint at $18 /ea');
});

test('fillers are stripped', () => {
  const out = V.normalizeSpokenTranscript('you know um like please can you let\'s do add a line item');
  assert.ok(!out.includes('you know'));
  assert.ok(!out.includes('please'));
  assert.ok(!out.includes('let\'s do'));
  assert.strictEqual(out, 'line item');
});

// ---- normalizeNumbers (addresses / proper nouns) ----------------------------

test('normalizeNumbers preserves casing and converts numbers only', () => {
  assert.strictEqual(V.normalizeNumbers('Fourteen Hundred Mockingbird Lane'), '1400 Mockingbird Lane');
  assert.strictEqual(V.normalizeNumbers('the client\'s home is 1400 Mockingbird Lane'), "the client's home is 1400 Mockingbird Lane");
});

test('normalizeNumbers never applies pricing/dimensional idioms', () => {
  // "Half Moon Bay" must not become "1/2 Moon Bay"; "5 Point" must not get /ea.
  assert.strictEqual(V.normalizeNumbers('Half Moon Bay'), 'Half Moon Bay');
  assert.strictEqual(V.normalizeNumbers('Five Points'), '5 Points');
  assert.ok(!V.normalizeNumbers('18 bucks road').includes('$'));
  assert.ok(!V.normalizeNumbers('18 bucks road').includes('/ea'));
});

test('normalizeNumbers strips fillers', () => {
  assert.strictEqual(V.normalizeNumbers('you know 1400 Mockingbird Lane'), '1400 Mockingbird Lane');
});

// ---- toTitleCase / cleanLeadingStopWords (addresses, titles, names) ---------

test('toTitleCase capitalizes words and preserves digit prefixes', () => {
  assert.strictEqual(V.toTitleCase('1400 Mockingbird Lane'), '1400 Mockingbird Lane');
  assert.strictEqual(V.toTitleCase('123 Main St'), '123 Main St');
  assert.strictEqual(V.toTitleCase('master bath remodel'), 'Master Bath Remodel');
  assert.strictEqual(V.toTitleCase('john o\'brien'), 'John O\'brien');
  assert.strictEqual(V.toTitleCase('1/2" drywall'), '1/2" Drywall');
  assert.strictEqual(V.toTitleCase(''), '');
  assert.strictEqual(V.toTitleCase(null), '');
});

test('toTitleCase leaves already-formatted names intact', () => {
  assert.strictEqual(V.toTitleCase('Five Points Roofing'), 'Five Points Roofing');
  assert.strictEqual(V.toTitleCase('Half Moon Bay'), 'Half Moon Bay');
});

test('cleanLeadingStopWords strips connective prefixes', () => {
  assert.strictEqual(V.cleanLeadingStopWords('is 1400 Mockingbird Lane'), '1400 Mockingbird Lane');
  assert.strictEqual(V.cleanLeadingStopWords('should be Master Bath Remodel'), 'Master Bath Remodel');
  assert.strictEqual(V.cleanLeadingStopWords('at 77 Oak Ave'), '77 Oak Ave');
  assert.strictEqual(V.cleanLeadingStopWords('the main office'), 'main office');
  assert.strictEqual(V.cleanLeadingStopWords('it\'s the garage'), 'the garage');
  assert.strictEqual(V.cleanLeadingStopWords('in the kitchen'), 'the kitchen');
  assert.strictEqual(V.cleanLeadingStopWords('and the garage'), 'the garage');
  assert.strictEqual(V.cleanLeadingStopWords('1400 Mockingbird Lane'), '1400 Mockingbird Lane');
  assert.strictEqual(V.cleanLeadingStopWords('  is 500 Broadway  '), '500 Broadway');
});
