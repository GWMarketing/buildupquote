/* Lexical voice normalizer: converts spoken transcripts into standardized
 * tokens before the smart_voice_parser regexes run.
 *
 * Two pipelines:
 *   normalizeSpokenTranscript(raw) - full: lowercase, number-words -> digits
 *     (incl. compounds like "twenty five" -> 25, "two hundred fifty" -> 250),
 *     pricing idioms ("18 bucks a piece" -> "$18 /ea"), dimensional idioms
 *     ("half inch" -> 1/2"), filler stripping. Used for LINE ITEMS.
 *   normalizeNumbers(raw)          - case-preserving number-words -> digits +
 *     filler stripping only (no pricing/dimensional idioms). Used for ADDRESS
 *     values so "Fourteen Hundred Mockingbird Lane" -> "1400 Mockingbird Lane"
 *     but "Half Moon Bay" is never mangled into "1/2 Moon Bay".
 *
 * Pure functions, no DOM deps: exposed as window.BQVoiceNormalizer and as a
 * CommonJS module for Node tests (tests/js/voice_normalizer.test.js).
 */
(function () {
  'use strict';

  // Exact spec table plus everything needed to rebuild numbers from speech.
  var NUMBER_WORDS = {
    zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7,
    eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13,
    fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
    nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60,
    seventy: 70, eighty: 80, ninety: 90, hundred: 100,
  };

  var TENS_VALUES = {
    twenty: 20, thirty: 30, forty: 40, fifty: 50,
    sixty: 60, seventy: 70, eighty: 80, ninety: 90,
  };

  var ONES_WORDS = {
    one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
    nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14,
    fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
  };

  // Filler / conversational fluff (spec list + a few real-world extras).
  var FILLER_RE = /\b(you know|um+|uh+|like|roughly|around|about|approximately|please|can you|could you|let'?s add|let us add|let'?s do|add a|add an|add|maybe|kind of|sort of)\b/gi;

  var ONES_SRC = 'one|two|three|four|five|six|seven|eight|nine';
  var TEENS_SRC = 'ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen';
  var TENS_SRC = 'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety';

  /** Word-based numbers to digits, case-insensitive, case-preserving outside
   *  the converted tokens. "twenty five" -> 25, "two hundred fifty" -> 250. */
  function convertNumberWords(text) {
    var t = String(text == null ? '' : text);
    // 1) Tens compounds first so "twenty five" becomes 25 (not "20 5").
    t = t.replace(new RegExp('\\b(' + TENS_SRC + ')[-\\s]+(' + ONES_SRC + '|' + TEENS_SRC + ')\\b', 'gi'),
      function (m, ten, one) {
        return String(TENS_VALUES[ten.toLowerCase()] + ONES_WORDS[one.toLowerCase()]);
      });
    // 2) Bare single words (hundred is handled by its own passes below).
    t = t.replace(new RegExp('\\b(' + ONES_SRC + '|' + TEENS_SRC + '|' + TENS_SRC + '|zero)\\b', 'gi'),
      function (m, w) {
        return String(NUMBER_WORDS[w.toLowerCase()]);
      });
    // 3) "N hundred and M" / "N hundred M" -> full number ("2 hundred 50" -> 250).
    t = t.replace(/\b(\d{1,3})\s+hundred(?:\s+and)?\s+(\d{1,3})\b/gi,
      function (m, h, r) { return String(Number(h) * 100 + Number(r)); });
    // 4) Bare hundreds ("2 hundred" -> 200, "a hundred" -> 100).
    t = t.replace(/\b(?:a|(\d{1,3}))\s+hundred\b/gi,
      function (m, h) { return String((h ? Number(h) : 1) * 100); });
    return t;
  }

  function stripFillers(text) {
    return String(text || '')
      .replace(FILLER_RE, '')
      .replace(/\s*,\s*,?\s*/g, ', ')
      .replace(/\s+/g, ' ')
      .replace(/^[\s,;:.()\-—]+|[\s,;:.()\-—]+$/g, '')
      .trim();
  }

  /** Full lexical pipeline for line-item speech. */
  function normalizeSpokenTranscript(raw) {
    var text = String(raw == null ? '' : raw).toLowerCase().trim();
    text = convertNumberWords(text);
    text = text
      // "18 bucks" / "16 dollars" -> "$18" / "$16"
      .replace(/\b(\d+(?:\.\d+)?)\s*(bucks|dollars)\b/g, '$$$1')
      // bare "bucks"/"dollars" -> "$"
      .replace(/\b(bucks|dollars)\b/g, '$')
      // "$30 a gallon" / "4 per unit" -> "$30 /ea" (price context only, so
      // quantity speech like "add a gallon of paint" is left alone)
      .replace(/\b(\$?\d+(?:\.\d+)?)\s+(?:a|per)\s+(?:piece|each|unit|gallon|sheet)\b/g, '$1 /ea')
      // unambiguous "each" -> "/ea"
      .replace(/\beach\b/g, '/ea')
      // dimensional: "half inch" / "1/2 inch" -> 1/2", "five eighths" -> 5/8"
      .replace(/\b(half|1\/2)\s*inch\b/g, '1/2"')
      .replace(/\b5\s*eighths(?:\s*inch)?\b/g, '5/8"')
      .replace(/\b5\/8\s*inch\b/g, '5/8"');
    return stripFillers(text);
  }

  /** Case-preserving numbers + fillers only (addresses / proper nouns). */
  function normalizeNumbers(raw) {
    return stripFillers(convertNumberWords(String(raw == null ? '' : raw).trim()));
  }

  /** Title-case for addresses, titles, client names. Digit-prefixed tokens
   *  ("1400", "1/2\"") are preserved verbatim. */
  function toTitleCase(str) {
    if (!str) return '';
    return String(str)
      .toLowerCase()
      .split(' ')
      .filter(Boolean)
      .map(function (word) {
        if (/^\d/.test(word)) return word;
        return word.charAt(0).toUpperCase() + word.slice(1);
      })
      .join(' ');
  }

  /** Strip leading prepositions / connective stop-words left behind by
   *  mid-sentence pauses ("is 1400 Mockingbird" -> "1400 Mockingbird"). */
  function cleanLeadingStopWords(str) {
    return String(str || '').trim()
      .replace(/^(is|it is|it's|at|be|to|the|should be|a|for)\s+/i, '')
      .trim();
  }

  var BQVoiceNormalizer = {
    NUMBER_WORDS: NUMBER_WORDS,
    convertNumberWords: convertNumberWords,
    normalizeSpokenTranscript: normalizeSpokenTranscript,
    normalizeNumbers: normalizeNumbers,
    toTitleCase: toTitleCase,
    cleanLeadingStopWords: cleanLeadingStopWords,
  };

  if (typeof window !== 'undefined') window.BQVoiceNormalizer = BQVoiceNormalizer;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQVoiceNormalizer;
  }
})();
