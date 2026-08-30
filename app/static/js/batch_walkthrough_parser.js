/* Staged walkthrough parser: the recorder accumulates the WHOLE session's
 * final transcripts into a buffer (no real-time dispatch, no interim logic).
 * On stop, parseWalkthroughTranscript() runs once over the full transcript and
 * returns structured data for the review drawer's "Apply to Quote" step:
 *
 *   { title, client_name, site_address,
 *     rooms: { "<Room Label>": { line_items: [...], assemblies: [...] } },
 *     general_notes: [...], skipped_items: [{ originalText, reason }],
 *     problems: [...], matched, raw }
 *
 * Line items carry { qty, unit, description, unit_cost, type, trade } where
 * trade is guessed from the description via the trade catalog; assemblies
 * carry { trade (spoken term), trade_key (canonical slug), length, width,
 * height }. Items are bucketed under the room they were spoken for ("in the
 * kitchen", "for the master bedroom", "bedroom 2", or a sentence that starts
 * with the room). `skipped_items` records corrections/discards and unresolved
 * construction phrases (with a reason); `problems` carries warn-level
 * diagnostics (no-price lines, missing assembly dimensions) for the review
 * step.
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

  var ROOM_CUE_SRC = 'moving\\s+over\\s+to|moving\\s+to|heading\\s+(?:to|into)|over\\s+to|toward|in|for|at|on|into|near|inside|outside|upstairs|downstairs';
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
    desc = desc
      .replace(/^(?:line\s+)?(?:item|line|new\s+(?:item|line))\s*/i, '')
      .replace(/^(?:of|of the|for)\s+/i, '')
      .replace(/^[,:;\-—\s]+|[,:;\-—\s]+$/g, '')
      .trim();
    return {
      qty: qty, unit: unit, description: capitalize(desc || 'Custom Item'),
      unit_cost: unitCost, type: 'material', trade: matchTrade(desc),
    };
  }

  // ---- Intent anchors -------------------------------------------------------
  var ADDRESS_RE = /\b(?:(?:his|her|the|site|client(?:'s)?)\s+)?address(?:\s+of\s+the\s+(?:client(?:'s)?\s+)?home)?(?:\s*(?:is\s*[,:.]?\s*|should\s+be\s*[,:.]?\s*|:)\s*)?(.+)$/i;
  var CLIENT_RE = /\b(?:set\s+)?(?:the\s+)?(?:client(?:'s)?(?:\s+name)?|appliance(?:'s)?(?:\s+name)?|customer)\s+(?:is\s+|to\s+|name\s+is\s+|should\s+be\s+)?([A-Za-z][A-Za-z0-9.'-]*?(?:\s+[A-Za-z][A-Za-z0-9.'-]*?){0,3}?)(?=\s+(?:is|next|the|then|and|in|at|for|with|should|his|her)\b|$)/i;
  var TITLE_RE = /(?:quote\s+(?:name|title)(?:\s+should\s+be|\s+is)?|call\s+this\s+quote)\s+(.+)/i;
  var TRADE_ALT_SRC = tradeAlternationSource();
  var ASSEMBLY_RE = new RegExp('\\b(' + TRADE_ALT_SRC + ')\\b\\s+(\\d+(?:\\.\\d+)?)\\s*(?:x|by|×|\\*)\\s*(\\d+(?:\\.\\d+)?)(?:\\s+(\\d+(?:\\.\\d+)?)\\s*(?:ft|foot|feet|ceiling|ceilings|high))?', 'i');
  var INTENT_ANCHOR_RE = new RegExp('\\b(?:quote\\s+(?:name|title)|call\\s+this\\s+quote|' +
    '(?:(?:his|her|the|site|client(?:\'s)?|appliance(?:\'s)?)\\s*)?address|' +
    '(?:client(?:\'s)?|appliance(?:\'s)?|customer)(?:\\s+name)?(?=\\s+(?:is|should\\s+be)\\b)|' +
    '(?:line\\s+item|new\\s+(?:line|item))|' +
    '\\d+(?:\\.\\d+)?\\s+(?:' + UNIT_RE_SRC + ')\\b|' +
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
      title: null, client_name: null, site_address: null,
      rooms: {}, general_notes: [], skipped_items: [], problems: [],
      matched: 0, raw: raw,
    };
    if (!raw) return data;

    _itemSeq = 0;
    _lastAdd = null;
    _lastRoom = null;
    var sentences = splitSentences(raw);
    var currentRoom = 'Main Area';
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
        var segNorm = normalizeTranscript(seg);
        // A later clause in a run-on final can still re-scope ("bathroom 2
        // needs 10 gallons of paint" following kitchen items).
        var segRoom = detectRoom(segNorm) || detectRoom(seg);
        if (segRoom) currentRoom = segRoom;

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
        // Unmatched meaningful speech: unresolved construction phrases get a
        // reason; everything else lands in general_notes (never dropped). A
        // room-marker phrase ("next room is the kitchen") sets context and is
        // neither.
        if (seg.length >= 3 && !segNorm.match(ASSEMBLY_RE) &&
            !detectRoom(seg) && !detectRoom(segNorm)) {
          if (CONSTRUCTION_HINT_RE.test(segNorm)) {
            data.skipped_items.push({
              originalText: seg,
              reason: 'Could not resolve complete quantity, unit, or pricing parameters',
            });
          } else {
            data.general_notes.push(seg);
          }
        }
      }

      // Per-sentence assembly sweep: catches every "drywall 12 by 14 9 ft"
      // (and multiple per sentence) while still allowing "scratch that" to
      // retract the assembly it just added.
      var asmRe = new RegExp(ASSEMBLY_RE.source, 'gi');
      var am;
      while ((am = asmRe.exec(normalized)) !== null) {
        roomBucket(data, currentRoom).assemblies.push({
          id: 'asm-' + (++_itemSeq),
          checked: true,
          trade: am[1].toLowerCase(),
          trade_key: matchTrade(am[1]),
          length: parseFloat(am[2]),
          width: parseFloat(am[3]),
          height: am[4] ? parseFloat(am[4]) : 8,
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
    data.matched = (data.title ? 1 : 0) + (data.client_name ? 1 : 0) +
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
  };
  if (typeof window !== 'undefined') window.BQWalkthroughParser = BQWalkthroughParser;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQWalkthroughParser;
  }
})();

