/* Trade vocabulary for the voice walkthrough parser + quote builder.
 *
 * Mirrors the proposed lib/tradeCatalog.js contract — canonical trade slugs
 * plus every spoken synonym — exposed as window.BQTradeCatalog and as a
 * CommonJS module so the Node suite can require() it directly (this app has
 * no lib/ tree; static JS lives in app/static/js/).
 *
 * matchTrade() is deliberately the "did the words mention it?" substring
 * matcher: drywall -> 'drywall', sheetrock -> 'drywall', LVP -> 'flooring',
 * rewire -> 'electrical'. Trade order matters — 'flooring' is checked before
 * the dedicated 'tile' key, so a bare "tile" resolves to 'flooring' (the
 * assembly matcher keeps the raw spoken word and falls back to it when the
 * canonical slug doesn't line up with a seeded assembly).
 */
(function () {
  'use strict';

  var TRADE_SYNONYMS = {
    drywall: ['drywall', 'sheetrock', 'gypsum', 'plaster', 'wallboard', 'taping', 'mudding'],
    framing: ['framing', 'studs', 'timber', 'rough carpentry', 'partition wall', 'wood framing'],
    paint: ['paint', 'painting', 'primer', 'stain', 'coating', 'sealer', 'interior paint', 'exterior paint'],
    flooring: ['flooring', 'vinyl plank', 'laminate', 'hardwood', 'tile', 'carpet', 'lvp', 'lvt', 'linoleum'],
    tile: ['tile', 'tiling', 'backsplash', 'shower tile', 'porcelain', 'ceramic'],
    electrical: ['electrical', 'wiring', 'lighting', 'fixtures', 'receptacles', 'switches', 'conduit', 'subpanel'],
    plumbing: ['plumbing', 'rough plumbing', 'drain', 'fixtures', 'vanity hookup', 'toilet', 'shower valve', 'pex'],
    trim: ['trim', 'baseboard', 'molding', 'casing', 'crown molding', 'door trim', 'finish carpentry'],
    insulation: ['insulation', 'batt insulation', 'fiberglass', 'rockwool', 'rigid foam', 'spray foam'],
    roofing: ['roofing', 'shingles', 'underlayment', 'flashing'],
    siding: ['siding', 'hardie', 'vinyl siding', 'cladding'],
    demolition: ['demolition', 'demo', 'tear out', 'removal', 'strip out'],
    concrete: ['concrete', 'slab', 'footing', 'flatwork', 'masonry', 'foundation'],
  };

  function matchTrade(word) {
    if (!word) return null;
    var clean = String(word).toLowerCase().trim();
    for (var trade in TRADE_SYNONYMS) {
      if (Object.prototype.hasOwnProperty.call(TRADE_SYNONYMS, trade) &&
          TRADE_SYNONYMS[trade].some(function (syn) { return clean.indexOf(syn) !== -1; })) {
        return trade;
      }
    }
    return null;
  }

  /** Title-case a trade slug for display ("drywall" -> "Drywall"). */
  function describeTrade(trade) {
    if (!trade) return '';
    return String(trade)
      .toLowerCase()
      .split(' ')
      .filter(Boolean)
      .map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); })
      .join(' ');
  }

  var BQTradeCatalog = {
    TRADE_SYNONYMS: TRADE_SYNONYMS,
    matchTrade: matchTrade,
    describeTrade: describeTrade,
  };

  if (typeof window !== 'undefined') window.BQTradeCatalog = BQTradeCatalog;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQTradeCatalog;
  }
})();
