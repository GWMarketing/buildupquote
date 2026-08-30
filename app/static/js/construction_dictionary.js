/* Centralized construction dictionary: unit normalization + spoken-material
 * alias mapping for the voice walkthrough pipeline.
 *
 * Mirrors the proposed lib/constructionDictionary.js contract (this app has
 * no lib/ tree; static JS lives in app/static/js/ and is exposed as
 * window.BQConstructionDictionary + a CommonJS module for Node tests).
 *
 * CONSTRUCTION_UNITS maps spoken/short unit words to the normalized symbol
 * stored on quote lines ('gallons' -> 'gal', 'pieces' -> 'ea', 'sqft' ->
 * 'sq ft'). TRADE_MATERIAL_ALIASES rewrites raw descriptions into canonical
 * material names ("sheetrock" -> "Drywall", "pot lights" -> "Recessed LED
 * Fixtures", "2 by 4" -> "2x4 SPF Studs") so the same product spoken
 * differently still aggregates to one row and gets a clean trade guess.
 */
(function () {
  'use strict';

  // Spoken number words -> digits, shared by the transcript normalizer so the
  // "two by four" -> "2 by 4" conversion happens before material aliases run.
  var NUMBER_WORDS = {
    zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
    eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13,
    fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
    nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60,
    seventy: 70, eighty: 80, ninety: 90, hundred: 100,
  };

  var CONSTRUCTION_UNITS = {
    // Area & Length
    'sq ft': 'sq ft', 'sqft': 'sq ft', 'square feet': 'sq ft', 'square foot': 'sq ft', 'squares': 'sq',
    'lin ft': 'lin ft', 'linear feet': 'lin ft', 'linear foot': 'lin ft', 'lf': 'lin ft',
    'sq m': 'm2', 'square meters': 'm2', 'square meter': 'm2', 'm2': 'm2',
    // Quantity units
    'sheets': 'sheet', 'sheet': 'sheet',
    'studs': 'stud', 'stud': 'stud', 'sticks': 'stick', 'stick': 'stick',
    'boxes': 'box', 'box': 'box',
    'bags': 'bag', 'bag': 'bag',
    'buckets': 'bucket', 'bucket': 'bucket', 'pails': 'bucket',
    'gallons': 'gal', 'gallon': 'gal', 'gals': 'gal',
    'rolls': 'roll', 'roll': 'roll',
    'bundles': 'bundle', 'bundle': 'bundle',
    'fixtures': 'fixture', 'fixture': 'fixture',
    'units': 'ea', 'pieces': 'ea', 'piece': 'ea', 'each': 'ea',
    'hours': 'hr', 'hour': 'hr', 'hrs': 'hr', 'hr': 'hr',
  };

  // Common trade abbreviations and phonetic speech misinterpretations, applied
  // in order to raw line-item descriptions after quantity/unit extraction.
  var TRADE_MATERIAL_ALIASES = [
    // Drywall & Masonry
    { pattern: /\b(sheet\s*rock|gypsum\s*board|wall\s*board)\b/gi, replacement: 'Drywall' },
    { pattern: /\b(green\s*board|moisture\s*resistant\s*drywall)\b/gi, replacement: 'Greenboard Drywall' },
    { pattern: /\b(purple\s*board)\b/gi, replacement: 'Purple Board Drywall' },
    { pattern: /\b(cement\s*board|durock|hardie\s*backer|wonder\s*board)\b/gi, replacement: 'Cement Backer Board' },
    { pattern: /\b(joint\s*compound|drywall\s*mud|all\s*purpose\s*mud|sheetrock\s*mud)\b/gi, replacement: 'Joint Compound' },

    // Electrical
    { pattern: /\b(romex\s*wire|romex|14\s*2|14\/2|12\s*2|12\/2)\b/gi, replacement: 'Romex NM-B Wire' },
    { pattern: /\b(recessed\s*lights?|pot\s*lights?|can\s*lights?|wafer\s*lights?|recessed\s*leds?)\b/gi, replacement: 'Recessed LED Fixtures' },
    { pattern: /\b(gfci\s*outlet|gfi\s*receptacle|gfi)\b/gi, replacement: 'GFCI Receptacle' },
    { pattern: /\b(single\s*pole\s*switch|light\s*switch|decora\s*switch)\b/gi, replacement: 'Single Pole Switch' },

    // Plumbing
    { pattern: /\b(pex\s*tubing|pex\s*pipe|pex\s*a|pex\s*b)\b/gi, replacement: 'PEX Tubing' },
    { pattern: /\b(shark\s*bite\s*fittings?)\b/gi, replacement: 'SharkBite Push Fitting' },
    { pattern: /\b(thin\s*set|thinset\s*mortar|tile\s*mortar)\b/gi, replacement: 'Thinset Mortar' },
    { pattern: /\b(shower\s*pan\s*liner|kerdi\s*membrane|redgard)\b/gi, replacement: 'Waterproofing Membrane' },

    // Framing & Lumber
    { pattern: /\b(two\s*by\s*fours?|2\s*by\s*4s?)\b/gi, replacement: '2x4 SPF Studs' },
    { pattern: /\b(two\s*by\s*sixes?|2\s*by\s*6s?)\b/gi, replacement: '2x6 SPF Framing' },
    { pattern: /\b(sub\s*floor|osb\s*sheathing|cdx\s*plywood)\b/gi, replacement: '3/4" OSB Subfloor' },
    { pattern: /\b(pressure\s*treated\s*(?:lumber|wood)?|pt\s*lumber)\b/gi, replacement: 'Pressure Treated Lumber' },

    // Insulation & Paint
    { pattern: /\b(r\s*13|r\s*19|r\s*30|fiberglass\s*batt|batt\s*insulation)\b/gi, replacement: 'Batt Insulation' },
    { pattern: /\b(pva\s*primer|drywall\s*primer)\b/gi, replacement: 'Drywall PVA Primer' },
    { pattern: /\b(semi\s*gloss|satin\s*finish|eggshell\s*finish)\b/gi, replacement: 'Interior Wall Paint' },
  ];

  /** Normalize a detected unit word to its canonical symbol. */
  function normalizeUnit(word) {
    var w = String(word || '').toLowerCase().replace(/\s+/g, ' ').trim();
    if (Object.prototype.hasOwnProperty.call(CONSTRUCTION_UNITS, w)) {
      return CONSTRUCTION_UNITS[w];
    }
    return w;
  }

  /** Rewrite a raw description through every material alias, in order.
   *  Idempotent: an alias whose replacement is NOT matched by its own pattern
   *  ("recessed led" -> "Recessed LED Fixtures") is skipped when the canonical
   *  name is already present, so re-running on canonical text never
   *  double-applies. Self-consistent aliases ("batt insulation" -> "Batt
   *  Insulation") re-apply safely every time. */
  function aliasMaterial(text) {
    if (!text) return '';
    var out = String(text);
    var outLower = out.toLowerCase();
    TRADE_MATERIAL_ALIASES.forEach(function (entry) {
      var repLower = entry.replacement.toLowerCase();
      var selfConsistent = new RegExp('^(?:' + entry.pattern.source + ')$', 'i').test(entry.replacement);
      if (!selfConsistent && outLower.indexOf(repLower) !== -1) return;
      out = out.replace(entry.pattern, entry.replacement);
      outLower = out.toLowerCase();
    });
    return out.replace(/\s{2,}/g, ' ').trim();
  }

  var BQConstructionDictionary = {
    NUMBER_WORDS: NUMBER_WORDS,
    CONSTRUCTION_UNITS: CONSTRUCTION_UNITS,
    TRADE_MATERIAL_ALIASES: TRADE_MATERIAL_ALIASES,
    normalizeUnit: normalizeUnit,
    aliasMaterial: aliasMaterial,
  };

  if (typeof window !== 'undefined') window.BQConstructionDictionary = BQConstructionDictionary;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQConstructionDictionary;
  }
})();
