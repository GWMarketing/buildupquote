/* Staged walkthrough parser: the recorder accumulates the WHOLE session's
 * final transcripts into a buffer (no real-time dispatch, no interim logic).
 * On stop, parseWalkthroughTranscript() runs once over the full transcript and
 * returns structured data for the review drawer's "Apply to Quote" step:
 *
 *   { quote_title, client_name, site_address,
 *     rooms: { "<Room Label>": { line_items: [...], assemblies: [...] } },
 *     client_scope_notes: [...], internal_crew_notes: [...],
 *     skipped_items: [{ originalText, reason }],
 *     problems: [...], matched, raw }
 *
 * Line items carry { qty, unit, description, unit_cost, type, trade } where
 * trade is guessed from the description via the trade catalog and the
 * description is canonicalized by the construction dictionary's material
 * aliases ("sheetrock" -> "Drywall"); assemblies carry { trade (spoken term),
 * trade_key (canonical slug), length, width, height }. Items are bucketed
 * under the room they were spoken for ("in the kitchen", "for the master
 * bedroom", "bedroom 2", or a sentence that starts with the room).
 * `skipped_items` records corrections/discards and unresolved construction
 * fragments (with a reason); notes split into client scope vs private
 * internal/crew notes; `problems` carries warn-level diagnostics (no-price
 * lines, missing assembly dimensions) for the review step.
 *
 * Pure functions, no DOM deps: window.BQWalkthroughParser + CommonJS for Node
 * tests (tests/js/batch_walkthrough_parser.test.js). Depends on the shared
 * lexical normalizer (voice_normalizer.js) and the trade catalog
 * (trade_catalog.js).
 */
(function () {
  'use strict';

  var Normalizer = null;
  if (typeof window !== 'undefined' && window.BQVoiceNormalizer) {
    Normalizer = window.BQVoiceNormalizer;
  } else if (typeof require === 'function') {
    try { Normalizer = require('./voice_normalizer.js'); } catch (e) { /* keep null */ }
  }

  var TradeCatalog = null;
  if (typeof window !== 'undefined' && window.BQTradeCatalog) {
    TradeCatalog = window.BQTradeCatalog;
  } else if (typeof require === 'function') {
    try { TradeCatalog = require('./trade_catalog.js'); } catch (e) { /* keep null */ }
  }

  var ConstructionDictionary = null;
  if (typeof window !== 'undefined' && window.BQConstructionDictionary) {
    ConstructionDictionary = window.BQConstructionDictionary;
  } else if (typeof require === 'function') {
    try { ConstructionDictionary = require('./construction_dictionary.js'); } catch (e) { /* keep null */ }
  }

  var FILLER_RE = /\b(you know|um+|uh+|like|roughly|around|about|approximately|please|can you|could you|let'?s add|let us add|let'?s do|add a|add an|add|maybe|kind of|sort of|all right|alright|so|we are gonna need|we'?ll need|we will need)\b/gi;

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
  // Spoken/short unit words -> normalized symbol. The centralized dictionary
  // (construction_dictionary.js) is the source of truth; this local map is the
  // fallback when the dictionary isn't loaded, and stays in sync with it.
  var UNIT_ALIASES = {
    'sq ft': 'sq ft', 'sqft': 'sq ft', 'square feet': 'sq ft', 'square foot': 'sq ft', 'squares': 'sq',
    'lin ft': 'lin ft', 'linear feet': 'lin ft', 'linear foot': 'lin ft', 'lf': 'lin ft',
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
  var UNIT_RE_SRC = [
    'sq\\s*ft', 'square\\s*feet?',
    'linear\\s*ft', 'linear\\s*feet?', 'lin\\s*ft', 'l\\s*f', 'lf',
    'gallons?', 'gals?', 'sheets?', 'studs?', 'sticks?', 'boxes?', 'bags?',
    'buckets?', 'pails?', 'rolls?', 'bundles?', 'fixtures?', 'squares?',
    'pieces?', 'units?', 'hours?', 'hrs?',
  ].join('|');
  var UNIT_RE = new RegExp('(' + UNIT_RE_SRC + ')\\b', 'i');

  function normalizeUnit(word) {
    var w = String(word || '').toLowerCase().replace(/\s+/g, ' ').trim();
    if (ConstructionDictionary && ConstructionDictionary.normalizeUnit) {
      return ConstructionDictionary.normalizeUnit(w);
    }
    return UNIT_ALIASES[w] || w;
  }

  /** Canonical material name for a raw description (dictionary aliases). */
  function aliasMaterial(text) {
    return (ConstructionDictionary && ConstructionDictionary.aliasMaterial)
      ? ConstructionDictionary.aliasMaterial(text)
      : String(text || '');
  }

  function capitalize(word) {
    return String(word || '').charAt(0).toUpperCase() + String(word || '').slice(1);
  }

  function escapeRegExp(str) {
    return String(str || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // ---- Trade recognition (expanded vocabulary from trade_catalog.js) --------
  var FALLBACK_TRADES = [
    'drywall', 'sheetrock', 'gypsum', 'plaster', 'wallboard', 'taping', 'mudding',
    'framing', 'studs', 'stud', 'timber', 'rough carpentry', 'partition wall',
    'partition', 'wood framing', 'paint', 'painting', 'primer', 'stain',
    'flooring', 'vinyl plank', 'laminate', 'hardwood', 'tile', 'carpet', 'lvp',
    'lvt', 'linoleum', 'backsplash', 'shower tile', 'porcelain', 'ceramic',
    'electrical', 'wiring', 'lighting', 'fixtures', 'receptacles', 'switches',
    'conduit', 'subpanel', 'plumbing', 'rough plumbing', 'drain', 'vanity hookup',
    'toilet', 'shower valve', 'pex', 'trim', 'baseboard', 'molding', 'casing',
    'crown molding', 'door trim', 'finish carpentry', 'insulation',
    'batt insulation', 'fiberglass', 'rockwool', 'rigid foam', 'spray foam',
    'roofing', 'shingles', 'underlayment', 'flashing', 'siding', 'hardie',
    'vinyl siding', 'cladding', 'demolition', 'demo', 'tear out', 'removal',
    'strip out', 'concrete', 'slab', 'footing', 'flatwork', 'masonry',
    'foundation',
  ];

  /** Canonical trade slug for a description / spoken term (or null). */
  function matchTrade(text) {
    if (TradeCatalog && TradeCatalog.matchTrade) return TradeCatalog.matchTrade(text);
    if (!text) return null;
    var clean = String(text).toLowerCase().trim();
    for (var i = 0; i < FALLBACK_TRADES.length; i++) {
      if (clean.indexOf(FALLBACK_TRADES[i]) !== -1) return FALLBACK_TRADES[i];
    }
    return null;
  }

  /** Regex alternation source covering every trade synonym, longest-first. */
  function tradeAlternationSource() {
    var syns = [];
    var src = (TradeCatalog && TradeCatalog.TRADE_SYNONYMS) || null;
    if (src) {
      Object.keys(src).forEach(function (k) {
        (src[k] || []).forEach(function (s) {
          if (syns.indexOf(s) === -1) syns.push(s);
        });
      });
    } else {
      syns = FALLBACK_TRADES.slice();
    }
    syns.sort(function (a, b) { return b.length - a.length; });
    return syns.map(escapeRegExp).join('|');
  }

  // ---- Room-context detection -----------------------------------------------
  // Rooms only register when the word follows a location cue ("in the
  // kitchen", "for the master bedroom", "at bedroom 2") or a bare "the
  // kitchen" — so "add 20 deck boards" never re-tags the current room.
  var ROOM_WORDS = [
    'master bedroom', 'guest bedroom', 'spare bedroom', 'master bathroom',
    'guest bathroom', 'powder room', 'half bathroom', 'laundry room',
    'utility room', 'walk-in closet', 'walk in closet', 'living room',
    'family room', 'great room', 'dining room', 'sitting room', 'crawl space',
    'master bath', 'guest bath', 'half bath', 'hallway bath', 'hall bath',
    'mud room', 'mudroom', 'bathroom', 'bath room', 'bedroom', 'kitchen',
    'garage', 'basement', 'lower level', 'cellar', 'attic', 'hallway',
    'stairwell', 'staircase', 'landing', 'entryway', 'foyer', 'corridor',
    'closet', 'laundry', 'stairs', 'hall', 'office', 'study', 'den', 'porch',
    'deck', 'patio', 'balcony', 'veranda', 'exterior', 'outside', 'facade',
    'front yard', 'back yard', 'backyard', 'driveway', 'sidewalk', 'roof',
  ];

  var ROOM_ALT_SRC = ROOM_WORDS.slice()
    .sort(function (a, b) { return b.length - a.length; })
    .map(escapeRegExp)
    .join('|');

  var ROOM_CUE_SRC = 'moving\\s+over\\s+to|moving\\s+to|heading\\s+down\\s+to|heading\\s+(?:to|into)|over\\s+to|toward|in|for|at|on|into|near|inside|outside|upstairs|downstairs';
  var ROOM_NUM_RE = new RegExp('\\b(bedroom|bathroom|bath|garage|office|den)\\s+(\\d{1,2})(?!\\s*(?:' + UNIT_RE_SRC + ')\\b)', 'i');
  var ROOM_NUM_CUE_RE = new RegExp('\\b(?:' + ROOM_CUE_SRC + ')\\s+(?:(?:the|a|an|this|that)\\s+)?(' + ROOM_ALT_SRC + ')(?:\\s+(\\d{1,2})(?!\\s*(?:' + UNIT_RE_SRC + ')\\b))?\\b', 'i');
  var ROOM_THE_RE = new RegExp('\\bthe\\s+(' + ROOM_ALT_SRC + ')\\b', 'i');
  // A sentence that literally starts with a room word ("kitchen needs new
  // paint", "master bedroom has 15 sheets of drywall") re-scopes too.
  var ROOM_START_RE = new RegExp('^(?:the\\s+)?(' + ROOM_ALT_SRC + ')\\b', 'i');
  // Trailing location phrase stripped from line-item descriptions so
  // "drywall in the kitchen" and "drywall in the garage" stay distinct rows
  // (each tagged with its own room) instead of leaking the phrase into the
  // description.
  var ROOM_STRIP_RE = new RegExp('\\s+(?:in|for|at|on|into)\\s+(?:(?:the|a|an|this|that)\\s+)?(' +
    ROOM_ALT_SRC + '|(?:bedroom|bathroom|bath|garage|office|den)\\s+\\d{1,2})[\\s,.;:]*$', 'i');

  /** Return the display label of the room a phrase is scoped to, or null.
   *  Cue phrases win ("in the master bedroom" beats the bare "bedroom N" rule,
   *  which is what keeps "in the garage 10 gallons of paint" scoped to the
   *  Garage and not the phantom "Garage 10"). */
  function roomLabel(room, num) {
    return toTitleCaseText(num ? room + ' ' + num : room);
  }

  function detectRoom(text) {
    var t = String(text || '');
    var m = t.match(ROOM_NUM_CUE_RE);
    if (m) return roomLabel(m[1], m[2]);
    m = t.match(ROOM_THE_RE);
    if (m) return roomLabel(m[1], null);
    m = t.match(ROOM_NUM_RE);
    if (m) return roomLabel(m[1], m[2]);
    m = t.match(ROOM_START_RE);
    if (m) return roomLabel(m[1], null);
    return null;
  }

  function stripRoomPhrase(text) {
    return String(text || '').replace(ROOM_STRIP_RE, '').trim();
  }

  // "at $18 a piece", "18 bucks each", "for 40" ... (the normalizer turns these
  // into "$18 /ea" tokens; the legacy idioms stay as a defensive fallback).
  var COST_RE = /(?:\s+(?:at|for|@|around|roughly|about|is)\s*|\s+)?\$?(\d+(?:\.\d+)?)(?:\s*(?:(?:a|per)\s+(?:piece|each|unit|gallon|sheet|square|box|bag|roll)|each|bucks?(?:\s+(?:each|a\s+(?:piece|each)))?|dollars?(?:\s+(?:each|a\s+(?:piece|each)))?|\/ea))?\s*$/i;

  // Strict line-item triggers (a bare "item" word is NOT enough) + strict
  // [Number] + [Trade Unit] sequence + conversational blacklist.
  var LINE_TRIGGER_RE = /\b(?:line\s+item|new\s+(?:item|line)|add\s+(?:a\s+|an\s+)?(?:line\s+)?item|put\s+(?:down|in))\b/i;
  var QTY_UNIT_RE = new RegExp('\\b(?:\\d+(?:\\.\\d+)?|a|an)\\s+(?:' + UNIT_RE_SRC + ')\\b', 'i');
  var LINE_ITEM_BLACKLIST_RE = /\b(not going to|not sure|maybe|think|probably|okay|ok\b|whatever|doesn'?t|isn'?t|don'?t|won'?t|can'?t|no good)\b/i;

  function isLineItemPhrase(text) {
    if (LINE_TRIGGER_RE.test(text) || QTY_UNIT_RE.test(text)) return true;
    // Bare quantity + recognizable construction material ("8 recessed lights",
    // no unit word) still counts when the words alias to a canonical material
    // or match a trade. Dimensional phrases ("10 by 12") stay assembly-only.
    var bare = String(text || '').match(/(?:^|\D)(\d+(?:\.\d+)?)\s+(.+)$/);
    if (bare && bare[2].length >= 3 && !/\b\d+(?:\s*(?:x|by)\s*)\d+\b/i.test(text)) {
      var rest = bare[2];
      return aliasMaterial(rest) !== rest || !!matchTrade(rest);
    }
    return false;
  }

  function parseLineItem(text) {
    // The transcript is already normalized (number words -> digits, material
    // aliases -> canonical names) by the caller; this function only does the
    // structural quantity/unit/price extraction.
    var t = String(text == null ? '' : text);
    var unitCost = 0;
    var body = t;
    var costMatch = t.match(COST_RE);
    if (costMatch) {
      unitCost = parseFloat(costMatch[1]) || 0;
      body = t.slice(0, costMatch.index).trim();
    }
    var qty = 1;
    var unit = 'ea';
    var desc = body;
    var unitMatch = body.match(UNIT_RE);
    if (unitMatch) {
      // The quantity is the number immediately before the unit ("bedroom 2
      // needs 10 gallons of paint" -> 10, not 2).
      var qtyPre = body.slice(0, unitMatch.index).match(/(\d+(?:\.\d+)?)\s*$/);
      qty = qtyPre ? parseFloat(qtyPre[1]) || 1 : 1;
      unit = normalizeUnit(unitMatch[1]);
      desc = body.slice(unitMatch.index + unitMatch[0].length)
        .replace(/^(?:of|of the)\s+/i, '')
        .trim();
    } else {
      var qtyMatch = body.match(/^[^\d]*(\d+(?:\.\d+)?)/);
      if (qtyMatch) {
        qty = parseFloat(qtyMatch[1]) || 1;
        desc = body.slice(qtyMatch.index + qtyMatch[0].length).trim();
      }
    }
    // The unit word can be the tail of a canonical material name
    // ("Recessed LED Fixtures" ends in the unit word "fixtures"), which leaves
    // an empty description. Re-parse the whole body as a bare-quantity
    // material ("8 Recessed LED Fixtures" -> 8 ea) so the line isn't lost.
    if (!desc) {
      var bareQ = body.match(/(?:^|\D)(\d+(?:\.\d+)?)\s+(.+)$/);
      if (bareQ) {
        qty = parseFloat(bareQ[1]) || 1;
        desc = bareQ[2];
        unit = 'ea';
      }
    }
    desc = desc
      .replace(/^(?:line\s+)?(?:item|line|new\s+(?:item|line))\s*/i, '')
      .replace(/^(?:of|of the|for)\s+/i, '')
      .replace(/^[,:;\-—\s]+|[,:;\-—\s]+$/g, '')
      .trim();
    // Canonical material names ("sheetrock" -> "Drywall", "2 by 4" ->
    // "2x4 SPF Studs") so aggregation and the trade guess see one name. The
    // normalizer already does this at the lexical layer; this is idempotent.
    desc = aliasMaterial(desc) || desc;
    return {
      qty: qty, unit: unit, description: capitalize(desc || 'Custom Item'),
      unit_cost: unitCost, type: 'material', trade: matchTrade(desc),
    };
  }

  // ---- Intent anchors -------------------------------------------------------
  var ADDRESS_RE = /\b(?:(?:his|her|the|site|client(?:'s)?)\s+)?address(?:\s+of\s+the\s+(?:client(?:'s)?\s+)?home)?(?:\s*(?:is\s*[,:.]?\s*|should\s+be\s*[,:.]?\s*|:)\s*)?(.+)$/i;
  var CLIENT_RE = /\b(?:set\s+)?(?:the\s+)?(?:client(?:'s)?(?:\s+name)?|appliance(?:'s)?(?:\s+name)?|customer)\s+(?:is\s+|to\s+|name\s+is\s+|should\s+be\s+)?([A-Za-z][A-Za-z0-9.'-]*?(?:\s+[A-Za-z][A-Za-z0-9.'-]*?){0,3}?)(?=\s+(?:is|next|the|then|and|in|at|for|with|should|his|her)\b|$)/i;
  var TITLE_RE = /(?:quote\s+(?:name|title)(?:\s+should\s+be|\s+is)?|call\s+this\s+quote|project\s+is)\s+(.+)/i;
  var TRADE_ALT_SRC = tradeAlternationSource();
  var ASSEMBLY_RE = new RegExp('\\b(' + TRADE_ALT_SRC + ')\\b\\s+(\\d+(?:\\.\\d+)?)\\s*(?:x|by|×|\\*)\\s*(\\d+(?:\\.\\d+)?)' +
    '(?:\\s+(\\d+(?:\\.\\d+)?)\\s*(?:ft|foot|feet|ceiling|ceilings|high)|' +
    '\\s*(?:ft|foot)?\\s*with\\s+(\\d+(?:\\.\\d+)?)\\s*(?:ft|foot|feet|ceiling|ceilings|high))?', 'i');
  var INTENT_ANCHOR_RE = new RegExp('\\b(?:quote\\s+(?:name|title)|call\\s+this\\s+quote|project\\s+is|' +
    '(?:(?:his|her|the|site|client(?:\'s)?|appliance(?:\'s)?)\\s*)?address|' +
    '(?:client(?:\'s)?|appliance(?:\'s)?|customer)(?:\\s+name)?(?=\\s+(?:is|should\\s+be)\\b)|' +
    '(?:line\\s+item|new\\s+(?:line|item))|' +
    '(?:a|an|\\d+(?:\\.\\d+)?)\\s+(?:' + UNIT_RE_SRC + ')\\b|' +
    '(?:' + TRADE_ALT_SRC + ')(?=\\s*\\d+\\s*(?:x|by)\\b))', 'gi');

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
  // Correction / discard phrases: the sentence is skipped and logged to
  // skipped_items; a retraction also removes the last item so "15 gallons of
  // paint. scratch that." doesn't leave a phantom row.
  var CORRECTION_RE = /\b(scratch that|never mind|nevermind|cancel that|forget it|ignore that|take that off|don'?t add|don'?t worry|not going to work|actually no)\b/i;
  var RETRACTION_RE = /\b(scratch that|cancel that|never mind|nevermind|forget it|ignore that|take that off)\b/i;
  // Construction-y words that signal an intended line item that never fully
  // resolved ("we need a gallon", "40 bucks for that") -> skipped_items.
  var CONSTRUCTION_HINT_RE = /\b(need|needs|needed|gallons?|sheets?|boxes?|bags?|rolls?|pieces?|units?|studs?|bucks|dollars|sq\s*ft|linear\s*ft|hours?|hrs?)\b/i;
  // Private job-site / sub-contractor concerns -> internal_crew_notes.
  var INTERNAL_NOTE_RE = /\b(crew|subpanel|sub out|capacity|joists|insulation sub|plumbing stack|moisture|inspect before|foundation crack|subfloor|permit)\b/i;

  /** Split a master transcript into sentences on [.!?] boundaries plus compound
   *  clause connectors (", and", ", plus", ";", bare "plus"), so a run-on
   *  final like "15 gallons of paint, plus 20 sheets drywall" yields two
   *  sentences (the recorder also joins finals with ". "). */
  function splitSentences(text) {
    return String(text || '')
      .replace(/([.!?])\s+/g, '$1|---SEP---|')
      .replace(/\b(?:,\s*(?:and|plus|also|as well as)|;\s*|\bplus\b)\s+/gi, '|---SEP---|')
      .split('|---SEP---|')
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
  }

  var _itemSeq = 0;
  var _lastAdd = null;   // 'line' | 'asm' for retraction ordering
  var _lastRoom = null;  // room label the last item was pushed under

  function roomBucket(data, room) {
    if (!data.rooms[room]) data.rooms[room] = { line_items: [], assemblies: [] };
    return data.rooms[room];
  }

  function retractLast(data) {
    var bucket = _lastRoom ? data.rooms[_lastRoom] : null;
    if (_lastAdd === 'asm' && bucket && bucket.assemblies.length) bucket.assemblies.pop();
    else if (bucket && bucket.line_items.length) bucket.line_items.pop();
    else {
      // Fallback: walk rooms backwards for anything to retract.
      var keys = Object.keys(data.rooms);
      for (var i = keys.length - 1; i >= 0; i--) {
        var b = data.rooms[keys[i]];
        if (_lastAdd === 'asm' && b.assemblies.length) { b.assemblies.pop(); break; }
        if (b.line_items.length) { b.line_items.pop(); break; }
        if (b.assemblies.length) { b.assemblies.pop(); break; }
      }
    }
    _lastAdd = null;
  }

  function parseWalkthroughTranscript(transcript) {
    var raw = String(transcript || '').trim();
    var data = {
      quote_title: null, client_name: null, site_address: null,
      rooms: {}, client_scope_notes: [], internal_crew_notes: [],
      skipped_items: [], problems: [], matched: 0, raw: raw,
    };
    if (!raw) return data;

    _itemSeq = 0;
    _lastAdd = null;
    _lastRoom = null;
    var sentences = splitSentences(raw);
    var currentRoom = 'General Area';
    roomBucket(data, currentRoom);

    for (var s = 0; s < sentences.length; s++) {
      var sentence = sentences[s];

      // Corrections / discards: skip the sentence and log it; a retraction
      // also removes the last line item / assembly so "15 gallons of paint.
      // scratch that." doesn't leave a phantom row.
      if (CORRECTION_RE.test(sentence)) {
        data.skipped_items.push({
          originalText: sentence,
          reason: 'Discarded by user correction phrase (e.g. "scratch that", "don\'t add")',
        });
        if (RETRACTION_RE.test(sentence)) retractLast(data);
        continue;
      }

      var cleaned = cleanFillers(sentence);
      var normalized = normalizeTranscript(cleaned);
      // Room context: "in the kitchen", "for the master bedroom", "bedroom 2",
      // or a bare "the garage" all re-scope the items that follow. "add 20
      // deck boards" (no cue) keeps the previous room.
      var roomNow = detectRoom(normalized) || detectRoom(cleaned);
      if (roomNow) currentRoom = roomNow;
      var segments = segmentIntents(normalized);

      for (var i = 0; i < segments.length; i++) {
        var seg = segments[i];
        // Segments come from the already-normalized sentence; re-normalizing
        // would lowercase canonical alias names ("GFCI Receptacle" ->
        // "gfci receptacle") and corrupt them, so use the segment verbatim.
        var segNorm = seg;
        // A later clause in a run-on final can still re-scope ("bathroom 2
        // needs 10 gallons of paint" following kitchen items).
        var segRoom = detectRoom(segNorm) || detectRoom(seg);
        if (segRoom) currentRoom = segRoom;

        var titleMatch = seg.match(TITLE_RE);
        if (titleMatch && titleMatch[1]) {
          var cleanTitle = toTitleCaseText(cleanLeading(titleMatch[1]));
          if (cleanTitle.length > 2 && !/^(is|the|be)$/i.test(cleanTitle)) data.quote_title = cleanTitle;
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
          // Drop a trailing location phrase ("drywall in the kitchen" ->
          // "drywall") so aggregation keys on the material, not the room.
          item.description = stripRoomPhrase(item.description) || item.description;
          var itemDesc = String(item.description || '').trim();
          if (!itemDesc || itemDesc.length < 2 || itemDesc === 'Custom Item' ||
              LINE_ITEM_BLACKLIST_RE.test(segNorm) || LINE_ITEM_BLACKLIST_RE.test(itemDesc)) {
            continue;
          }
          // Aggregate identical rows within the room (same material + price —
          // drywall in two rooms stays in its own bucket).
          var bucket = roomBucket(data, currentRoom);
          var existing = null;
          for (var j = 0; j < bucket.line_items.length; j++) {
            var x = bucket.line_items[j];
            if (x.unit === item.unit && x.description === item.description &&
                x.unit_cost === item.unit_cost) {
              existing = x; break;
            }
          }
          if (existing) {
            existing.qty += item.qty;
          } else {
            item.id = 'line-' + (++_itemSeq);
            item.checked = true;
            _lastAdd = 'line';
            _lastRoom = currentRoom;
            bucket.line_items.push(item);
          }
          continue;
        }
        // Unmatched meaningful speech: private crew/sub concerns, unresolved
        // construction phrases, or short fragments get reasons; longer
        // client-facing phrases land in client_scope_notes (never dropped). A
        // room-marker phrase ("next room is the kitchen") sets context and is
        // none of these.
        if (seg.length >= 3 && !segNorm.match(ASSEMBLY_RE) &&
            !detectRoom(seg) && !detectRoom(segNorm)) {
          var cleanNote = seg.replace(/^(?:note|notes|site\s+notes?|crew\s+notes?|general\s+notes?)[:\s-]*/i, '').trim();
          if (INTERNAL_NOTE_RE.test(segNorm) || INTERNAL_NOTE_RE.test(cleanNote)) {
            data.internal_crew_notes.push(cleanNote || seg);
          } else if (CONSTRUCTION_HINT_RE.test(segNorm)) {
            data.skipped_items.push({
              originalText: seg,
              reason: 'Could not resolve complete quantity, unit, or pricing parameters',
            });
          } else if (seg.split(/\s+/).length < 4) {
            data.skipped_items.push({
              originalText: seg,
              reason: 'Fragment lacked complete trade item or dimension parameters',
            });
          } else {
            data.client_scope_notes.push(cleanNote || seg);
          }
        }
      }

      // Per-sentence assembly sweep: catches every "drywall 12 by 14 9 ft"
      // (and multiple per sentence) while still allowing "scratch that" to
      // retract the assembly it just added.
      var asmRe = new RegExp(ASSEMBLY_RE.source, 'gi');
      var am;
      while ((am = asmRe.exec(normalized)) !== null) {
        var asmLen = parseFloat(am[2]);
        var asmWid = parseFloat(am[3]);
        // Skip degenerate matches ("drywall 0 by 0") without breaking the
        // global-sweep lastIndex contract.
        if (!(asmLen > 0) || !(asmWid > 0)) {
          if (am.index === asmRe.lastIndex) asmRe.lastIndex++;
          continue;
        }
        roomBucket(data, currentRoom).assemblies.push({
          id: 'asm-' + (++_itemSeq),
          checked: true,
          trade: am[1].toLowerCase(),
          trade_key: matchTrade(am[1]),
          length: asmLen,
          width: asmWid,
          height: am[4] ? parseFloat(am[4]) : (am[5] ? parseFloat(am[5]) : 8),
        });
        _lastAdd = 'asm';
        _lastRoom = currentRoom;
        if (am.index === asmRe.lastIndex) asmRe.lastIndex++;
      }
    }

    var lineCount = 0;
    var asmCount = 0;
    Object.keys(data.rooms).forEach(function (k) {
      lineCount += data.rooms[k].line_items.length;
      asmCount += data.rooms[k].assemblies.length;
    });
    data.matched = (data.quote_title ? 1 : 0) + (data.client_name ? 1 : 0) +
      (data.site_address ? 1 : 0) + lineCount + asmCount;

    // ---- Problem diagnostics for the review drawer --------------------------
    Object.keys(data.rooms).forEach(function (k) {
      data.rooms[k].line_items.forEach(function (it) {
        if (!it.unit_cost) {
          data.problems.push({
            level: 'warn',
            text: '"' + it.description + '" has no price spoken — will apply at $0',
          });
        }
      });
      data.rooms[k].assemblies.forEach(function (a) {
        if (!a.length || !a.width) {
          data.problems.push({
            level: 'warn',
            text: capitalize(a.trade) + ' assembly is missing dimensions',
          });
        }
      });
    });
    return data;
  }

  var BQWalkthroughParser = {
    parseWalkthroughTranscript: parseWalkthroughTranscript,
    segmentIntents: segmentIntents,
    parseLineItem: parseLineItem,
    isLineItemPhrase: isLineItemPhrase,
    cleanFillers: cleanFillers,
    detectRoom: detectRoom,
    stripRoomPhrase: stripRoomPhrase,
    aliasMaterial: aliasMaterial,
  };
  if (typeof window !== 'undefined') window.BQWalkthroughParser = BQWalkthroughParser;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQWalkthroughParser;
  }
})();

