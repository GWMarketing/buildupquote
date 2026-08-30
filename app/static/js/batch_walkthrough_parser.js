/* Staged walkthrough parser: the recorder accumulates the WHOLE session's
 * final transcripts into a buffer (no real-time dispatch, no interim logic).
 * On stop, parseWalkthroughTranscript() runs once over the full transcript and
 * returns structured data for the review drawer's "Apply to Quote" step:
 *
 *   { title, client_name, site_address, line_items: [...] (aggregated),
 *     assemblies: [...], notes: [...], matched, raw }
 *
 * Pure functions, no DOM deps: window.BQWalkthroughParser + CommonJS for Node
 * tests (tests/js/batch_walkthrough_parser.test.js). Depends on the shared
 * lexical normalizer (voice_normalizer.js).
 */
(function () {
  'use strict';

  var Normalizer = null;
  if (typeof window !== 'undefined' && window.BQVoiceNormalizer) {
    Normalizer = window.BQVoiceNormalizer;
  } else if (typeof require === 'function') {
    try { Normalizer = require('./voice_normalizer.js'); } catch (e) { /* keep null */ }
  }

  var FILLER_RE = /\b(you know|um+|uh+|like|roughly|around|about|approximately|please|can you|could you|let'?s add|let us add|let'?s do|add a|add an|add|maybe|kind of|sort of|all right|alright|so)\b/gi;

  function cleanFillers(text) {
    return String(text || '')
      .replace(FILLER_RE, '')
      .replace(/\s*,\s*,?\s*/g, ', ')
      .replace(/\s+/g, ' ')
      .replace(/^[\s,;:.()\-—]+|[\s,;:.()\-—]+$/g, '')
      .trim();
  }

  function normalizeTranscript(text) {
    return (Normalizer && Normalizer.normalizeSpokenTranscript)
      ? Normalizer.normalizeSpokenTranscript(text)
      : cleanFillers(text);
  }

  function normalizeNumbers(text) {
    return (Normalizer && Normalizer.normalizeNumbers)
      ? Normalizer.normalizeNumbers(text)
      : cleanFillers(text);
  }

  function toTitleCaseText(text) {
    return (Normalizer && Normalizer.toTitleCase) ? Normalizer.toTitleCase(text) : text;
  }

  function cleanLeading(text) {
    return (Normalizer && Normalizer.cleanLeadingStopWords)
      ? Normalizer.cleanLeadingStopWords(text)
      : String(text || '').trim();
  }

  // ---- Entity & unit recognition -------------------------------------------
  var UNIT_ALIASES = {
    'gallons': 'gal', 'gallon': 'gal', 'gals': 'gal',
    'sheets': 'sheet', 'sheet': 'sheet',
    'studs': 'stud', 'stud': 'stud',
    'boxes': 'box', 'box': 'box',
    'bags': 'bag', 'bag': 'bag',
    'rolls': 'roll', 'roll': 'roll',
    'squares': 'square', 'square': 'square',
    'pieces': 'piece', 'piece': 'piece',
    'units': 'unit', 'unit': 'unit',
    'hours': 'hr', 'hour': 'hr', 'hrs': 'hr', 'hr': 'hr',
    'sq ft': 'sq ft', 'square feet': 'sq ft', 'square foot': 'sq ft',
    'sq m': 'm2', 'square meters': 'm2', 'square meter': 'm2', 'm2': 'm2',
    'linear ft': 'lin ft', 'linear feet': 'lin ft', 'lin ft': 'lin ft',
    'lf': 'lin ft',
  };
  var UNIT_RE_SRC = [
    'sq\\s*ft', 'square\\s*feet?', 'sq\\s*m', 'square\\s*meters?', 'm2',
    'linear\\s*ft', 'linear\\s*feet?', 'lin\\s*ft', 'l\\s*f', 'lf',
    'gallons?', 'gals?', 'sheets?', 'studs?', 'boxes?', 'bags?', 'rolls?',
    'squares?', 'pieces?', 'units?', 'hours?', 'hrs?',
  ].join('|');
  var UNIT_RE = new RegExp('(' + UNIT_RE_SRC + ')\\b', 'i');

  function normalizeUnit(word) {
    var w = String(word || '').toLowerCase().replace(/\s+/g, ' ');
    return UNIT_ALIASES[w] || w;
  }

  function capitalize(word) {
    return String(word || '').charAt(0).toUpperCase() + String(word || '').slice(1);
  }

  // "at $18 a piece", "18 bucks each", "for 40" ... (the normalizer turns these
  // into "$18 /ea" tokens; the legacy idioms stay as a defensive fallback).
  var COST_RE = /(?:\s+(?:at|for|@|around|roughly|about|is)\s*|\s+)?\$?(\d+(?:\.\d+)?)(?:\s*(?:(?:a|per)\s+(?:piece|each|unit|gallon|sheet|square|box|bag|roll)|each|bucks?(?:\s+(?:each|a\s+(?:piece|each)))?|dollars?(?:\s+(?:each|a\s+(?:piece|each)))?|\/ea))?\s*$/i;

  // Strict line-item triggers (a bare "item" word is NOT enough) + strict
  // [Number] + [Trade Unit] sequence + conversational blacklist.
  var LINE_TRIGGER_RE = /\b(?:line\s+item|new\s+(?:item|line)|add\s+(?:a\s+|an\s+)?(?:line\s+)?item|put\s+(?:down|in))\b/i;
  var QTY_UNIT_RE = new RegExp('\\b\\d+(?:\\.\\d+)?\\s+(?:' + UNIT_RE_SRC + ')\\b', 'i');
  var LINE_ITEM_BLACKLIST_RE = /\b(not going to|not sure|maybe|think|probably|okay|ok\b|whatever|doesn'?t|isn'?t|don'?t|won'?t|can'?t|no good)\b/i;

  function isLineItemPhrase(text) {
    return LINE_TRIGGER_RE.test(text) || QTY_UNIT_RE.test(text);
  }

  function parseLineItem(text) {
    var t = normalizeTranscript(text);
    var unitCost = 0;
    var body = t;
    var costMatch = t.match(COST_RE);
    if (costMatch) {
      unitCost = parseFloat(costMatch[1]) || 0;
      body = t.slice(0, costMatch.index).trim();
    }
    var qty = 1;
    var unit = 'unit';
    var desc = body;
    var qtyMatch = body.match(/^[^\d]*(\d+(?:\.\d+)?)/);
    var unitMatch = body.match(UNIT_RE);
    if (unitMatch && qtyMatch) {
      qty = parseFloat(qtyMatch[1]) || 1;
      unit = normalizeUnit(unitMatch[1]);
      desc = body.slice(unitMatch.index + unitMatch[0].length)
        .replace(/^(?:of|of the)\s+/i, '')
        .trim();
    } else if (qtyMatch) {
      qty = parseFloat(qtyMatch[1]) || 1;
      desc = body.slice(qtyMatch.index + qtyMatch[0].length).trim();
    }
    desc = desc
      .replace(/^(?:line\s+)?(?:item|line|new\s+(?:item|line))\s*/i, '')
      .replace(/^(?:of|of the|for)\s+/i, '')
      .replace(/^[,:;\-—\s]+|[,:;\-—\s]+$/g, '')
      .trim();
    return {
      qty: qty, unit: unit, description: capitalize(desc || 'Custom Item'),
      unit_cost: unitCost, type: 'material',
    };
  }

  // ---- Intent anchors -------------------------------------------------------
  var ADDRESS_RE = /\b(?:(?:his|her|the|site|client(?:'s)?)\s+)?address(?:\s+of\s+the\s+(?:client(?:'s)?\s+)?home)?(?:\s*(?:is\s*[,:.]?\s*|should\s+be\s*[,:.]?\s*|:)\s*)?(.+)$/i;
  var CLIENT_RE = /\b(?:set\s+)?(?:the\s+)?(?:client(?:'s)?(?:\s+name)?|appliance(?:'s)?(?:\s+name)?|customer)\s+(?:is\s+|to\s+|name\s+is\s+|should\s+be\s+)?([A-Za-z][A-Za-z0-9\s.'-]{1,40})$/i;
  var TITLE_RE = /(?:quote\s+(?:name|title)(?:\s+should\s+be|\s+is)?|call\s+this\s+quote)\s+(.+)/i;
  var ASSEMBLY_RE = /\b(drywall|framing|stud|partition|tile|flooring|paint|trim)\b\s+(\d+(?:\.\d+)?)\s*(?:x|by|×|\*)\s*(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?)\s*(?:ft|foot|feet|ceiling|ceilings|high))?/i;
  var INTENT_ANCHOR_RE = new RegExp('\\b(?:quote\\s+(?:name|title)|call\\s+this\\s+quote|' +
    '(?:(?:his|her|the|site|client(?:\'s)?|appliance(?:\'s)?)\\s*)?address|' +
    '(?:client(?:\'s)?|appliance(?:\'s)?|customer)(?:\\s+name)?(?=\\s+(?:is|should\\s+be)\\b)|' +
    '(?:line\\s+item|new\\s+(?:line|item))|' +
    '\\d+(?:\\.\\d+)?\\s+(?:' + UNIT_RE_SRC + ')\\b|' +
    '(?:drywall|framing|stud|partition|tile|flooring|paint|trim)(?=\\s*\\d+\\s*(?:x|by)\\b))', 'gi');

  /** Split filler-cleaned speech into intent segments at anchor boundaries. */
  function segmentIntents(text) {
    var t = String(text || '').trim();
    if (!t) return [];
    var anchors = [];
    var re = INTENT_ANCHOR_RE;
    re.lastIndex = 0;
    var m;
    while ((m = re.exec(t)) !== null) {
      anchors.push(m.index);
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    if (!anchors.length) return [t];
    var segments = [];
    for (var i = 0; i < anchors.length; i++) {
      // The first segment keeps any leading context ("i think we need 15
      // gallons of paint") so conversational blacklist words aren't dropped
      // before the first qty/unit anchor.
      var start = (i === 0 && anchors[i] > 0) ? 0 : anchors[i];
      var end = (i + 1 < anchors.length) ? anchors[i + 1] : t.length;
      var seg = t.slice(start, end).trim();
      // Drop trailing connectives ("15 gallons of paint ... and") so a
      // following qty/unit anchor doesn't leave "and" in the cost capture.
      seg = seg.replace(/\s+(?:and|then)\s*$/i, '').trim();
      if (seg) segments.push(seg);
    }
    return segments;
  }

  // ---- Staged batch parse ----------------------------------------------------
  // Correction / retraction phrases: "scratch that" cancels the last item.
  var CORRECTION_RE = /\b(scratch that|never mind|nevermind|cancel that|forget it|ignore that|take that off|don'?t add)\b/i;
  var RETRACTION_RE = /\b(scratch that|cancel that|never mind|nevermind|forget it|ignore that|take that off)\b/i;

  /** Split a master transcript into sentences on [.!?] boundaries (the
   *  recorder joins finals with ". "). */
  function splitSentences(text) {
    return String(text || '')
      .replace(/([.!?])\s+/g, '$1|---SEP---|')
      .split('|---SEP---|')
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
  }

  var _itemSeq = 0;
  var _lastAdd = null;   // 'line' | 'asm' for retraction ordering

  function retractLast(data) {
    if (_lastAdd === 'asm' && data.assemblies.length) data.assemblies.pop();
    else if (data.line_items.length) data.line_items.pop();
    else if (data.assemblies.length) data.assemblies.pop();
    _lastAdd = null;
  }

  function parseWalkthroughTranscript(transcript) {
    var raw = String(transcript || '').trim();
    var data = {
      title: null, client_name: null, site_address: null,
      line_items: [], assemblies: [], notes: [], matched: 0, raw: raw,
    };
    if (!raw) return data;

    _itemSeq = 0;
    _lastAdd = null;
    var sentences = splitSentences(raw);

    for (var s = 0; s < sentences.length; s++) {
      var sentence = sentences[s];

      // Corrections: skip the sentence; a retraction also removes the last
      // line item / assembly so "15 gallons of paint. scratch that." doesn't
      // leave a phantom row.
      if (CORRECTION_RE.test(sentence)) {
        if (RETRACTION_RE.test(sentence)) retractLast(data);
        continue;
      }

      var cleaned = cleanFillers(sentence);
      var normalized = normalizeTranscript(cleaned);
      var segments = segmentIntents(normalized);

      for (var i = 0; i < segments.length; i++) {
        var seg = segments[i];
        var segNorm = normalizeTranscript(seg);

        var titleMatch = seg.match(TITLE_RE);
        if (titleMatch && titleMatch[1]) {
          var cleanTitle = toTitleCaseText(cleanLeading(titleMatch[1]));
          if (cleanTitle.length > 2 && !/^(is|the|be)$/i.test(cleanTitle)) data.title = cleanTitle;
          continue;
        }
        var addressMatch = seg.match(ADDRESS_RE);
        if (addressMatch && addressMatch[1]) {
          data.site_address = toTitleCaseText(cleanLeading(normalizeNumbers(addressMatch[1])));
          continue;
        }
        var clientMatch = seg.match(CLIENT_RE);
        if (clientMatch && clientMatch[1]) {
          data.client_name = toTitleCaseText(cleanLeading(cleanFillers(clientMatch[1])));
          continue;
        }
        if (isLineItemPhrase(segNorm)) {
          var item = parseLineItem(segNorm);
          var itemDesc = String(item.description || '').trim();
          if (!itemDesc || itemDesc.length < 2 || itemDesc === 'Custom Item' ||
              LINE_ITEM_BLACKLIST_RE.test(segNorm) || LINE_ITEM_BLACKLIST_RE.test(itemDesc)) {
            continue;
          }
          // Aggregate identical rows across the whole walkthrough.
          var existing = null;
          for (var j = 0; j < data.line_items.length; j++) {
            var x = data.line_items[j];
            if (x.unit === item.unit && x.description === item.description && x.unit_cost === item.unit_cost) {
              existing = x; break;
            }
          }
          if (existing) {
            existing.qty += item.qty;
          } else {
            item.id = 'line-' + (++_itemSeq);
            item.checked = true;
            _lastAdd = 'line';
            data.line_items.push(item);
          }
          continue;
        }
        // Unmatched meaningful speech -> notes (never silently dropped).
        if (seg.length >= 3 && !segNorm.match(ASSEMBLY_RE)) data.notes.push(seg);
      }

      // Per-sentence assembly sweep: catches every "drywall 12 by 14 9 ft"
      // (and multiple per sentence) while still allowing "scratch that" to
      // retract the assembly it just added.
      var asmRe = new RegExp(ASSEMBLY_RE.source, 'gi');
      var am;
      while ((am = asmRe.exec(normalized)) !== null) {
        data.assemblies.push({
          id: 'asm-' + (++_itemSeq),
          checked: true,
          trade: am[1].toLowerCase(),
          length: parseFloat(am[2]),
          width: parseFloat(am[3]),
          height: am[4] ? parseFloat(am[4]) : 8,
        });
        _lastAdd = 'asm';
        if (am.index === asmRe.lastIndex) asmRe.lastIndex++;
      }
    }

    data.matched = (data.title ? 1 : 0) + (data.client_name ? 1 : 0) +
      (data.site_address ? 1 : 0) + data.line_items.length + data.assemblies.length;
    return data;
  }

  var BQWalkthroughParser = {
    parseWalkthroughTranscript: parseWalkthroughTranscript,
    segmentIntents: segmentIntents,
    parseLineItem: parseLineItem,
    isLineItemPhrase: isLineItemPhrase,
    cleanFillers: cleanFillers,
  };
  if (typeof window !== 'undefined') window.BQWalkthroughParser = BQWalkthroughParser;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQWalkthroughParser;
  }
})();

