/* Natural Conversational Voice Parser for the quote builder.

   Turns informal speech ("the address of the client's home is, you know,
   1400 Mockingbird Lane" / "add a new line item, 15 gallons of paint at
   roughly $18 a piece") into structured field updates and line-item rows.

   Pipeline: strip conversational fillers -> contextual dispatcher
   (mic off / address / client name / line item / focused field / notes).
   Plain JS, no dependencies; exposed as window.BQSmartVoice and as a CommonJS
   module so the Node test suite can exercise it directly.
*/
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Step A: filler / conversational normalizer
  // ------------------------------------------------------------------
  var FILLER_RE = /\b(you know|um+|uh+|like|roughly|around|about|approximately|please|can you|could you|let'?s add|let us add|let'?s do|add a|add an|add|maybe|kind of|sort of|all right|alright|so)\b/gi;

  // Lexical normalizer (voice_normalizer.js): number words -> digits, pricing
  // idioms -> "$18 /ea", dimensional idioms, fillers. Resolved from the browser
  // global, or required directly under Node so the test suite stays standalone.
  var Normalizer = null;
  if (typeof window !== 'undefined' && window.BQVoiceNormalizer) {
    Normalizer = window.BQVoiceNormalizer;
  } else if (typeof require === 'function') {
    try { Normalizer = require('./voice_normalizer.js'); } catch (e) { /* keep null */ }
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
    return (Normalizer && Normalizer.toTitleCase)
      ? Normalizer.toTitleCase(text)
      : text;
  }

  function cleanLeading(text) {
    return (Normalizer && Normalizer.cleanLeadingStopWords)
      ? Normalizer.cleanLeadingStopWords(text)
      : String(text || '').trim();
  }

  function cleanFillers(text) {
    return String(text || '')
      .replace(FILLER_RE, '')
      .replace(/\s*,\s*,?\s*/g, ', ')
      .replace(/\s+/g, ' ')
      .replace(/^[\s,;:.()\-—]+|[\s,;:.()\-—]+$/g, '')
      .trim();
  }

  // ------------------------------------------------------------------
  // Step B: entity & unit recognition
  // ------------------------------------------------------------------
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
    // Multi-word units first so "square feet" isn't short-circuited by "square".
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

  // "at $18 a piece", "18 bucks each", "16 dollars each", "for 40" ...
  var COST_RE = /(?:\s+(?:at|for|@|around|roughly|about|is)\s*|\s+)?\$?(\d+(?:\.\d+)?)(?:\s*(?:(?:a|per)\s+(?:piece|each|unit|gallon|sheet|square|box|bag|roll)|each|bucks?(?:\s+(?:each|a\s+(?:piece|each)))?|dollars?(?:\s+(?:each|a\s+(?:piece|each)))?|\/ea))?\s*$/i;

  var LINE_TRIGGER_RE = /\b(line item|line items?|new item|new line|add item|an item|item)\b/i;
  var UNIT_TRIGGER_RE = new RegExp('\\b(?:' + UNIT_RE_SRC + ')\\b', 'i');

  function isLineItemPhrase(text) {
    return LINE_TRIGGER_RE.test(text) || UNIT_TRIGGER_RE.test(text);
  }

  function parseLineItem(text) {
    var t = normalizeTranscript(text);

    // Cost first: a trailing "$18 a piece" / "18 bucks each" / "for 40".
    var unitCost = 0;
    var body = t;
    var costMatch = t.match(COST_RE);
    if (costMatch) {
      unitCost = parseFloat(costMatch[1]) || 0;
      body = t.slice(0, costMatch.index).trim();
    }

    // Quantity + unit from the front.
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

    // Drop the "line item / new item" trigger prefix that remains in desc.
    desc = desc
      .replace(/^(?:line\s+)?(?:item|line|new\s+(?:item|line))\s*/i, '')
      .replace(/^(?:of|of the|for)\s+/i, '')
      .replace(/^[,:;\-—\s]+|[,:;\-—\s]+$/g, '')
      .trim();

    return {
      qty: qty,
      unit: unit,
      description: capitalize(desc || 'Custom Item'),
      unit_cost: unitCost,
      type: 'material',
    };
  }

  function capitalize(word) {
    return String(word || '').charAt(0).toUpperCase() + String(word || '').slice(1);
  }

  // ------------------------------------------------------------------
  // Contextual target anchors
  // ------------------------------------------------------------------
  // "his address is 1400 Mockingbird Ln", "site address should be 123 Main St",
  // "the address of the client's home is 500 Broadway", "address 77 Oak Ave".
  var ADDRESS_RE = /\b(?:(?:his|her|the|site|client(?:'s)?)\s+)?address(?:\s+of\s+the\s+(?:client(?:'s)?\s+)?home)?(?:\s*(?:is\s*[,:.]?\s*|should\s+be\s*[,:.]?\s*|:)\s*)?(.+)$/i;
  // "appliance name" is a common recognizer mishearing of "client name".
  var CLIENT_RE = /\b(?:set\s+)?(?:the\s+)?(?:client(?:'s)?(?:\s+name)?|appliance(?:'s)?(?:\s+name)?|customer)\s+(?:is\s+|to\s+|name\s+is\s+|should\s+be\s+)?([A-Za-z][A-Za-z0-9\s.'-]{1,40})$/i;
  var MIC_OFF_RE = /\b(mic off|turn off mic|turn the mic off|stop listening|stop mic|cancel voice|shut (?:the )?mic off|mute mic)\b/i;
  var TITLE_RE = /(?:quote\s+(?:name|title)(?:\s+should\s+be|\s+is)?|call\s+this\s+quote)\s+(.+)/i;

  // Multi-intent splitting: one utterance can carry several commands
  // ("client name is Brandon his address is 1846.5 a nuisance lane"). Each
  // intent anchor starts a new segment that the single-intent dispatcher
  // handles independently. Anchors are chosen so a name/address regex can't
  // swallow the following intent (e.g. the `$`-anchored client capture would
  // otherwise absorb " his address is ...").
  var INTENT_ANCHOR_RE = /\b(?:quote\s+(?:name|title)|call\s+this\s+quote|(?:(?:his|her|the|site|client(?:'s)?|appliance(?:'s)?)\s*)?address|(?:client(?:'s)?|appliance(?:'s)?|customer)(?:\s+name)?(?=\s+(?:is|should\s+be)\b)|(?:line\s+item|new\s+(?:line|item))|(?:drywall|framing|stud|partition|tile|flooring|paint|trim)(?=\s*\d+\s*(?:x|by)\b))/gi;

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
      if (m.index === re.lastIndex) re.lastIndex++;   // never loop on empty matches
    }
    if (!anchors.length) return [t];
    var segments = [];
    for (var i = 0; i < anchors.length; i++) {
      var end = (i + 1 < anchors.length) ? anchors[i + 1] : t.length;
      var seg = t.slice(anchors[i], end).trim();
      if (seg) segments.push(seg);
    }
    return segments;
  }

  function isMicOffPhrase(text) {
    return MIC_OFF_RE.test(String(text || ''));
  }

  // Parametric assembly with explicit dimensions: "drywall 12 by 14 9 ft ceiling",
  // "framing ten by twelve nine foot". Runs on the normalized text so spoken
  // numbers ("ten by twelve") already read as digits.
  var ASSEMBLY_RE = /\b(drywall|framing|stud|partition|tile|flooring|paint|trim)\b\s+(\d+(?:\.\d+)?)\s*(?:x|by|×|\*)\s*(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?)\s*(?:ft|foot|feet|ceiling|ceilings|high))?/i;

  // ------------------------------------------------------------------
  // Master contextual dispatcher: splits multi-intent utterances into
  // segments, applies each through the single-intent extractor, and falls
  // back to focused-field/notes when nothing structured matched.
  // ------------------------------------------------------------------
  function processConversationalVoice(transcript, handlers) {
    var raw = String(transcript || '').trim();
    if (!raw) return { action: 'none' };
    handlers = handlers || {};
    var notify = handlers.notify || function () {};

    // 1. Mic deactivation (terminal — takes the whole utterance)
    if (MIC_OFF_RE.test(raw)) {
      if (handlers.stopMic) handlers.stopMic();
      notify('Mic turned off');
      return { action: 'mic_off' };
    }

    var cleaned = cleanFillers(raw);
    var segments = segmentIntents(cleaned);
    var results = [];
    for (var i = 0; i < segments.length; i++) {
      var r = dispatchSingleIntent(segments[i], handlers);
      if (r) results.push(r);
    }

    if (results.length === 1) return results[0];
    if (results.length > 1) {
      // Several commands in one utterance: each handler already fired.
      return { action: 'multi', results: results, primary: results[0] };
    }

    // No structured intent: active focused-field injection, else notes.
    if (handlers.activeFocusedField && handlers.insertIntoActiveField) {
      handlers.insertIntoActiveField(raw);
      notify('Typed into ' + handlers.activeFocusedField);
      return { action: 'focused', field: handlers.activeFocusedField };
    }
    if (handlers.appendSiteNotes) handlers.appendSiteNotes(raw);
    notify('Noted (not matched to a field)');
    return { action: 'notes' };
  }

  /** Extract exactly ONE intent from a segment (or null if none matches). */
  function dispatchSingleIntent(segment, handlers) {
    var notify = handlers.notify || function () {};
    var normalized = normalizeTranscript(segment);

    // Quote title: "quote name should be Master Bath Remodel", "call this quote X"
    var titleMatch = segment.match(TITLE_RE);
    if (titleMatch && titleMatch[1]) {
      var cleanTitle = toTitleCaseText(cleanLeading(titleMatch[1]));
      if (cleanTitle.length > 2 && !/^(is|the|be)$/i.test(cleanTitle)) {
        if (handlers.setFieldValue) handlers.setFieldValue('title', cleanTitle);
        notify('Quote name set to: ' + cleanTitle);
        return { action: 'set_field', field: 'title', value: cleanTitle };
      }
    }

    // Site / client address (numbers -> digits, then title-cased)
    var addressMatch = segment.match(ADDRESS_RE);
    if (addressMatch && addressMatch[1]) {
      var addressValue = toTitleCaseText(cleanLeading(normalizeNumbers(addressMatch[1])));
      if (handlers.setFieldValue) handlers.setFieldValue('site_address', addressValue);
      notify('Address set to: ' + addressValue);
      return { action: 'set_field', field: 'site_address', value: addressValue };
    }

    // Client name (filler-only cleaning so "Five Points Roofing" survives)
    var clientMatch = segment.match(CLIENT_RE);
    if (clientMatch && clientMatch[1]) {
      var clientName = toTitleCaseText(cleanLeading(cleanFillers(clientMatch[1])));
      if (handlers.setFieldValue) handlers.setFieldValue('client_name', clientName);
      notify('Client set to: ' + clientName);
      return { action: 'set_field', field: 'client_name', value: clientName };
    }

    // Parametric assembly with explicit dimensions
    var assemblyDim = normalized.match(ASSEMBLY_RE);
    if (assemblyDim) {
      var asm = {
        trade: assemblyDim[1].toLowerCase(),
        length: parseFloat(assemblyDim[2]),
        width: parseFloat(assemblyDim[3]),
        height: assemblyDim[4] ? parseFloat(assemblyDim[4]) : 8,
      };
      if (handlers.insertAssembly) {
        handlers.insertAssembly(asm);
        notify(asm.trade + ' assembly: ' + asm.length + "' x " + asm.width +
          (asm.height ? ' @ ' + asm.height + 'ft' : '') + ' added');
        return { action: 'assembly', dims: asm };
      }
      if (handlers.matchAssembly) {
        var assemblyCode = handlers.matchAssembly(normalized);
        if (assemblyCode) {
          notify('Matched assembly: ' + assemblyCode + ' (' + asm.length + "' x " + asm.width + ')');
          return { action: 'assembly', code: assemblyCode, dims: asm };
        }
      }
    }

    // Keyword-only assembly ("drywall partition"). Skipped for line-item
    // phrases so "15 gallons of paint" still creates a line item.
    if (handlers.matchAssembly && !isLineItemPhrase(normalized)) {
      var assemblyCode2 = handlers.matchAssembly(normalized);
      if (assemblyCode2) {
        notify('Matched assembly: ' + assemblyCode2);
        return { action: 'assembly', code: assemblyCode2 };
      }
    }

    // Smart line item extraction
    if (isLineItemPhrase(normalized)) {
      var item = parseLineItem(normalized);
      if (handlers.addLineItem) handlers.addLineItem(item);
      notify(item.qty + ' ' + item.unit + ' ' + item.description + ' @ ' +
        (item.unit_cost ? '$' + item.unit_cost.toFixed(2) + '/' + item.unit : 'no cost') +
        ' added');
      return { action: 'add_line', item: item };
    }

    return null;
  }

  var BQSmartVoice = {
    cleanFillers: cleanFillers,
    normalizeUnit: normalizeUnit,
    isLineItemPhrase: isLineItemPhrase,
    parseLineItem: parseLineItem,
    processConversationalVoice: processConversationalVoice,
    parseVoiceInput: processConversationalVoice,  // spec-alias
    segmentIntents: segmentIntents,
    isMicOffPhrase: isMicOffPhrase,
    normalizeSpokenTranscript: normalizeTranscript,
    normalizeNumbers: normalizeNumbers,
  };

  if (typeof window !== 'undefined') window.BQSmartVoice = BQSmartVoice;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQSmartVoice;
  }
})();

