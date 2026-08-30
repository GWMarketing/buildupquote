/* Debounced continuous-voice commander: owns the speech accumulator, the
 * silence debounce, immediate termination flush, and the 150ms onend
 * auto-restart. Pure logic over injectable timers so Node's mock.timers / short
 * real timers can drive it deterministically (tests/js/voice_commander.test.js).
 *
 * Background-noise resilience (three gates):
 *   1. Confidence  - finals/interims below minConfidence are dropped (Chrome
 *                    scores background noise ~0-0.2; real speech ~0.5+).
 *   2. Junk words  - finals that are only fillers ("um", "like", "you know",
 *                    "the") never enter the buffer.
 *   3. Echo dedup  - consecutive identical finals are appended once, and the
 *                    same command text within sameCommandCooldownMs is not
 *                    re-dispatched (a noisy echo can't re-trigger a field set).
 * Plus the flush-time meaningfulness gate so accumulated filler never fires
 * the dispatcher (termination phrases bypass it and always flush).
 *
 * createVoiceCommander({
 *   onCommand(text)     dispatched with the accumulated buffer after the
 *                       silence debounce (or immediately on termination)
 *   onStatus(s)         {listening, interim} for the live "Heard: ..." pill
 *   onRestart()         invoked restartMs after onend, only while listening
 *   isTermination(text) "mic off" etc. -> immediate forced flush
 *   isJunk(text)        optional extra per-token junk predicate
 *   debounceMs          default 1800 (silence before dispatch)
 *   restartMs           default 150
 *   minConfidence       default 0.3 (0-1)
 *   sameCommandCooldownMs default 2500 (suppress identical re-dispatch)
 *   dedupeConsecutive   default true (echo dedup)
 *   setTimeout / clearTimeout  injectable (defaults to globalThis)
 * })
 */
(function () {
  'use strict';

  var DEFAULTS = {
    debounceMs: 1800,
    restartMs: 150,
    minConfidence: 0.3,
    sameCommandCooldownMs: 3000,
  };

  // Filler / background-noise vocabulary (nothing a real command needs).
  var JUNK_RE = /\b(um+|uh+|hmm+|like|you know|you know what|the|a|an|and|or|so|then|uh huh|yep|yeah|no|ok|okay|right|huh|mhm)\b/gi;

  /** Does this transcript contain real content (vs. pure filler/noise)? */
  function isMeaningful(text) {
    var t = String(text || '').trim();
    if (!t) return false;
    var stripped = t
      .replace(JUNK_RE, ' ')
      .replace(/[^a-z0-9$"'/.]/gi, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    return stripped.length >= 3;
  }

  function createVoiceCommander(opts) {
    var o = opts || {};
    var setTimer = o.setTimeout || (typeof globalThis !== 'undefined' && globalThis.setTimeout
      ? globalThis.setTimeout.bind(globalThis) : function () { return 0; });
    var clearTimer = o.clearTimeout || (typeof globalThis !== 'undefined' && globalThis.clearTimeout
      ? globalThis.clearTimeout.bind(globalThis) : function () {});
    var DEBOUNCE_MS = o.debounceMs || DEFAULTS.debounceMs;
    var RESTART_MS = o.restartMs || DEFAULTS.restartMs;
    var MIN_CONFIDENCE = (typeof o.minConfidence === 'number') ? o.minConfidence : DEFAULTS.minConfidence;
    var COOLDOWN_MS = o.sameCommandCooldownMs || DEFAULTS.sameCommandCooldownMs;
    var DEDUPE = (typeof o.dedupeConsecutive === 'boolean') ? o.dedupeConsecutive : true;

    var listening = false;
    var speechBuffer = '';
    var interimText = '';
    var lastFinal = '';
    var lastCommand = '';
    var lastCommandAt = 0;
    var debounceTimer = null;
    var restartTimer = null;

    function report() {
      if (o.onStatus) {
        o.onStatus({ listening: listening, interim: (interimText || speechBuffer).trim() });
      }
    }

    function clearDebounce() {
      if (debounceTimer) { clearTimer(debounceTimer); debounceTimer = null; }
    }

    function clearRestart() {
      if (restartTimer) { clearTimer(restartTimer); restartTimer = null; }
    }

    /** Per-token noise gate: confidence + junk words (+ optional caller test). */
    function passesGates(transcript, result) {
      var confidence = (typeof result[0].confidence === 'number') ? result[0].confidence : 1;
      if (confidence < MIN_CONFIDENCE) return false;
      if (o.isJunk && o.isJunk(transcript)) return false;
      return isMeaningful(transcript);
    }

    /** Dispatch the accumulated buffer, then clear it. force bypasses the
     *  meaningfulness + anti-repeat gates (used for termination phrases). */
    function flush(force) {
      clearDebounce();
      var text = speechBuffer.trim();
      speechBuffer = '';
      interimText = '';
      lastFinal = '';
      report();
      if (!text) return;
      if (!force && !isMeaningful(text)) return;                       // noise gate
      if (!force && COOLDOWN_MS && text === lastCommand &&
          (Date.now() - lastCommandAt) < COOLDOWN_MS) return;          // echo gate
      lastCommand = text;
      lastCommandAt = Date.now();
      if (o.onCommand) {
        try { o.onCommand(text); } catch (e) { /* dispatch errors never loop */ }
      }
    }

    /** Discard the buffer without dispatching. */
    function cancel() {
      clearDebounce();
      speechBuffer = '';
      interimText = '';
      lastFinal = '';
      report();
    }

    /** Feed one SpeechRecognition results list in. */
    function onResult(results) {
      if (!listening || !results) return;
      var finalText = '';
      var interim = '';
      for (var i = 0; i < results.length; i++) {
        var r = results[i];
        if (!r || !r[0]) continue;
        var transcript = (r[0].transcript || '').trim();
        if (!transcript) continue;
        if (r.isFinal) {
          if (!passesGates(transcript, r)) continue;                   // noise gate
          if (DEDUPE && transcript === lastFinal) continue;            // echo dedup
          lastFinal = transcript;
          finalText += transcript + ' ';
        } else {
          if (!passesGates(transcript, r)) continue;                   // noise gate
          interim += transcript + ' ';
        }
      }
      if (finalText) speechBuffer += ' ' + finalText.trim();
      interimText = interim.trim();
      report();

      if (!finalText && !interimText) return;

      // Termination commands flush immediately (the dispatcher stops the mic).
      if (o.isTermination && o.isTermination((speechBuffer + ' ' + interimText).trim())) {
        flush(true);
        return;
      }

      // Reset the silence window on every accepted token (final OR interim) so
      // a mid-sentence pause never dispatches a partial command.
      clearDebounce();
      debounceTimer = setTimer(function () {
        debounceTimer = null;
        flush(false);
      }, DEBOUNCE_MS);
    }

    /** Auto-keepalive: restart after onend, only while still listening. */
    function onEnd() {
      if (!listening) return;
      clearRestart();
      restartTimer = setTimer(function () {
        restartTimer = null;
        if (listening && o.onRestart) o.onRestart();
      }, RESTART_MS);
    }

    function start() {
      if (listening) return;
      listening = true;
      speechBuffer = '';
      interimText = '';
      lastFinal = '';
      clearDebounce();
      clearRestart();
      report();
    }

    function stop() {
      listening = false;
      clearDebounce();
      clearRestart();
      speechBuffer = '';
      interimText = '';
      lastFinal = '';
      report();
    }

    return {
      start: start,
      stop: stop,
      flush: flush,
      cancel: cancel,
      onResult: onResult,
      onEnd: onEnd,
      isListening: function () { return listening; },
      getBuffer: function () { return speechBuffer.trim(); },
    };
  }

  var BQVoiceCommander = {
    createVoiceCommander: createVoiceCommander,
    isMeaningful: isMeaningful,
    DEFAULTS: DEFAULTS,
  };
  if (typeof window !== 'undefined') window.BQVoiceCommander = BQVoiceCommander;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQVoiceCommander;
  }
})();
