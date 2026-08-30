'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { NUMBER_WORDS, CONSTRUCTION_UNITS, TRADE_MATERIAL_ALIASES, normalizeUnit, aliasMaterial } =
  require('../../app/static/js/construction_dictionary.js');

test('NUMBER_WORDS covers the spoken number table', () => {
  assert.strictEqual(NUMBER_WORDS.fourteen, 14);
  assert.strictEqual(NUMBER_WORDS.twenty, 20);
  assert.strictEqual(NUMBER_WORDS.ninety, 90);
  assert.strictEqual(NUMBER_WORDS.hundred, 100);
});

test('CONSTRUCTION_UNITS normalizes spoken and short unit words', () => {
  const cases = {
    'gallons': 'gal', 'gallon': 'gal', 'gals': 'gal',
    'sheets': 'sheet', 'sheet': 'sheet',
    'studs': 'stud', 'sticks': 'stick',
    'boxes': 'box', 'bags': 'bag', 'buckets': 'bucket', 'pails': 'bucket',
    'rolls': 'roll', 'bundles': 'bundle',
    'fixtures': 'fixture', 'fixture': 'fixture',
    'units': 'ea', 'pieces': 'ea', 'each': 'ea',
    'sq ft': 'sq ft', 'sqft': 'sq ft', 'square feet': 'sq ft', 'squares': 'sq',
    'lin ft': 'lin ft', 'linear feet': 'lin ft', 'lf': 'lin ft',
    'm2': 'm2', 'hours': 'hr',
  };
  for (const [word, out] of Object.entries(cases)) {
    assert.strictEqual(normalizeUnit(word), out, word);
  }
  // Unknown words pass through untouched.
  assert.strictEqual(normalizeUnit('wheelbarrow'), 'wheelbarrow');
});

test('TRADE_MATERIAL_ALIASES rewrites spoken materials to canonical names', () => {
  const cases = {
    'sheetrock': 'Drywall',
    'gypsum board': 'Drywall',
    'green board': 'Greenboard Drywall',
    'purple board': 'Purple Board Drywall',
    'cement board': 'Cement Backer Board',
    'durock': 'Cement Backer Board',
    'drywall mud': 'Joint Compound',
    'romex': 'Romex NM-B Wire',
    '14/2': 'Romex NM-B Wire',
    'pot lights': 'Recessed LED Fixtures',
    'recessed led': 'Recessed LED Fixtures',
    'gfi': 'GFCI Receptacle',
    'gfci outlet': 'GFCI Receptacle',
    'light switch': 'Single Pole Switch',
    'pex pipe': 'PEX Tubing',
    'sharkbite fittings': 'SharkBite Push Fitting',
    'thinset': 'Thinset Mortar',
    'redgard': 'Waterproofing Membrane',
    '2 by 4': '2x4 SPF Studs',
    'two by four': '2x4 SPF Studs',
    'osb sheathing': '3/4" OSB Subfloor',
    'pt lumber': 'Pressure Treated Lumber',
    'batt insulation': 'Batt Insulation',
    'pva primer': 'Drywall PVA Primer',
    'eggshell finish': 'Interior Wall Paint',
  };
  for (const [input, expected] of Object.entries(cases)) {
    assert.strictEqual(aliasMaterial(input), expected, input);
  }
});

test('aliasMaterial is idempotent on already-canonical text', () => {
  for (const canonical of ['Drywall', 'GFCI Receptacle', 'Romex NM-B Wire',
    'Recessed LED Fixtures', '2x4 SPF Studs', 'Drywall PVA Primer', 'Joint Compound']) {
    assert.strictEqual(aliasMaterial(canonical), canonical, canonical);
  }
});

test('TRADE_MATERIAL_ALIASES includes every cataloged trade group', () => {
  assert.strictEqual(TRADE_MATERIAL_ALIASES.length, 20);
});
