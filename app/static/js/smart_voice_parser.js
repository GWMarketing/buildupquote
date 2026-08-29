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
  var FILLER_RE = /\b(you know|um+|uh+|like|roughly|around|about|approximately|please|can you|could you|let'?s add|let us add|add a|add an|add|maybe|kind of|sort of)\b/gi;

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
    'gallons?', 'gals?', 'sheets?', 'studs?', 'boxes?', 'bags?', 'rolls?',
    'squares?', 'pieces?', 'units?', 'hours?', 'hrs?',
    'sq\\s*ft', 'square\\s*feet?', 'sq\\s*m', 'square\\s*meters?', 'm2',
    'linear\\s*ft', 'linear\\s*feet?', 'lin\\s*ft', 'l\\s*f', 'lf',
  ].join('|');
  var UNIT_RE = new RegExp('(' + UNIT_RE_SRC + ')\\b', 'i');

  function normalizeUnit(word) {
    var w = String(word || '').toLowerCase().replace(/\s+/g, ' ');
    return UNIT_ALIASES[w] || w;
  }

  // "at $18 a piece", "18 bucks each", "16 dollars each", "for 40" ...
  var COST_RE = /(?:\s+(?:at|for|@|around|roughly|about|is)\s*|\s+)?\$?(\d+(?:\.\d+)?)(?:\s*(?:(?:a|per)\s+(?:piece|each|unit|gallon|sheet|square|box|bag|roll)|each|bucks?(?:\s+(?:each|a\s+(?:piece|each)))?|dollars?(?:\s+(?:each|a\s+(?:piece|each)))?))?\s*$/i;

  var LINE_TRIGGER_RE = /\b(line item|line items?|new item|new line|add item|an item|item)\b/i;
  var UNIT_TRIGGER_RE = new RegExp('\\b(?:' + UNIT_RE_SRC + ')\\b', 'i');

  function isLineItemPhrase(text) {
    return LINE_TRIGGER_RE.test(text) || UNIT_TRIGGER_RE.test(text);
  }

  function parseLineItem(text) {
    var t = cleanFillers(text);

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
  var ADDRESS_RE = /\b(?:the\s+)?(?:site\s+|client(?:'s)?\s+(?:home\s+|house\s+)?)?address(?:\s+of\s+the\s+client(?:'s)?\s+home)?\s+(?:is\s*[,:.]?\s*|:\s*)?(.+)$/i;
  var CLIENT_RE = /\b(?:set\s+)?(?:the\s+)?(?:client(?:'s)?(?:\s+name)?|customer)\s+(?:is\s+|to\s+|name\s+is\s+)?([A-Za-z][A-Za-z0-9\s.'-]{1,40})$/i;
  var MIC_OFF_RE = /\b(mic off|turn off mic|turn the mic off|stop listening|shut (?:the )?mic off|mute mic)\b/i;

  // ------------------------------------------------------------------
  // Master contextual dispatcher
  // ------------------------------------------------------------------
  function processConversationalVoice(transcript, handlers) {
    var raw = String(transcript || '').trim();
    if (!raw) return { action: 'none' };
    handlers = handlers || {};
    var notify = handlers.notify || function () {};

    // 1. Mic deactivation
    if (MIC_OFF_RE.test(raw)) {
      if (handlers.stopMic) handlers.stopMic();
      notify('Mic turned off');
      return { action: 'mic_off' };
    }

    var cleaned = cleanFillers(raw);

    // 2. Site / client address
    var addressMatch = cleaned.match(ADDRESS_RE);
    if (addressMatch && addressMatch[1]) {
      var addressValue = cleanFillers(addressMatch[1]);
      if (handlers.setFieldValue) handlers.setFieldValue('site_address', addressValue);
      notify('Address set to: ' + addressValue);
      return { action: 'set_field', field: 'site_address', value: addressValue };
    }

    // 3. Client name
    var clientMatch = cleaned.match(CLIENT_RE);
    if (clientMatch && clientMatch[1]) {
      var clientName = cleanFillers(clientMatch[1]);
      if (handlers.setFieldValue) handlers.setFieldValue('client_name', clientName);
      notify('Client set to: ' + clientName);
      return { action: 'set_field', field: 'client_name', value: clientName };
    }

    // 4. Smart line item extraction
    if (isLineItemPhrase(cleaned)) {
      var item = parseLineItem(cleaned);
      if (handlers.addLineItem) handlers.addLineItem(item);
      notify(item.qty + ' ' + item.unit + ' ' + item.description + ' @ ' +
        (item.unit_cost ? '$' + item.unit_cost.toFixed(2) + '/' + item.unit : 'no cost') +
        ' added');
      return { action: 'add_line', item: item };
    }

    // 5. Assembly keyword match (builder flow: "drywall partition")
    if (handlers.matchAssembly) {
      var assemblyCode = handlers.matchAssembly(cleaned);
      if (assemblyCode) {
        notify('Matched assembly: ' + assemblyCode);
        return { action: 'assembly', code: assemblyCode };
      }
    }

    // 6. Active focused-field injection
    if (handlers.activeFocusedField && handlers.insertIntoActiveField) {
      handlers.insertIntoActiveField(raw);
      notify('Typed into ' + handlers.activeFocusedField);
      return { action: 'focused', field: handlers.activeFocusedField };
    }

    // 7. General notes fallback (never silently drop speech)
    if (handlers.appendSiteNotes) handlers.appendSiteNotes(raw);
    notify('Noted (not matched to a field)');
    return { action: 'notes' };
  }

  var BQSmartVoice = {
    cleanFillers: cleanFillers,
    normalizeUnit: normalizeUnit,
    isLineItemPhrase: isLineItemPhrase,
    parseLineItem: parseLineItem,
    processConversationalVoice: processConversationalVoice,
  };

  if (typeof window !== 'undefined') window.BQSmartVoice = BQSmartVoice;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQSmartVoice;
  }
})();

