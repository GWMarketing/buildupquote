/* Debounced continuous-voice commander: owns the speech accumulator, the
 * 1200ms silence debounce, immediate termination flush, and the 150ms onend
 * auto-restart. Pure logic over injectable timers so Node's mock.timers can
 * drive it deterministically (tests/js/voice_commander.test.js).
 *
 * createVoiceCommander({
 *   onCommand(text)     dispatched with the accumulated buffer after 1200ms of
 *                       silence (or immediately on a termination phrase)
 *   onStatus(s)         {listening, interim} for the live "Heard: ..." pill
 *   onRestart()         invoked 150ms after onend, only while listening
 *   isTermination(text) returns true for "mic off" etc. -> immediate flush
 *   debounceMs          default 1200
 *   restartMs           default 150
 *   setTimeout / clearTimeout  injectable (defaults to globalThis)
 * })
 *
 * Lifecycle: start() -> onResult()* / onEnd()* -> stop() (clears everything,
 * so a manual "mic off" can never be resurrected by a pending timer).
 */
(function () {
  'use strict';

  function createVoiceCommander(opts) {
    var o = opts || {};
    var setTimer = o.setTimeout || (typeof globalThis !== 'undefined' && globalThis.setTimeout
      ? globalThis.setTimeout.bind(globalThis) : function () { return 0; });
    var clearTimer = o.clearTimeout || (typeof globalThis !== 'undefined' && globalThis.clearTimeout
      ? globalThis.clearTimeout.bind(globalThis) : function () {});
    var DEBOUNCE_MS = o.debounceMs || 1200;
    var RESTART_MS = o.restartMs || 150;

    var listening = false;
    var speechBuffer = '';
    var interimText = '';
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

    /** Dispatch the accumulated buffer, then clear it (post-evaluation). */
    function flush() {
      clearDebounce();
      var text = speechBuffer.trim();
      speechBuffer = '';
      interimText = '';
      report();
      if (text && o.onCommand) {
        try { o.onCommand(text); } catch (e) { /* dispatch errors never loop */ }
      }
    }

    /** Discard the buffer without dispatching. */
    function cancel() {
      clearDebounce();
      speechBuffer = '';
      interimText = '';
      report();
    }

    /** Feed one SpeechRecognition results list in. */
    function onResult(results) {
      if (!listening || !results) return;
      var finalText = '';
      var interim = '';
      for (var i = 0; i < results.length; i++) {
        var r = results[i];
        if (!r) continue;
        var transcript = (r[0] && r[0].transcript) || '';
        if (r.isFinal) finalText += transcript + ' ';
        else interim += transcript + ' ';
      }
      if (finalText) speechBuffer += ' ' + finalText.trim();
      interimText = interim.trim();
      report();

      if (!finalText && !interimText) return;

      // Termination commands flush immediately (the dispatcher stops the mic).
      if (o.isTermination && o.isTermination((speechBuffer + ' ' + interimText).trim())) {
        flush();
        return;
      }

      // Reset the silence window on every spoken token (final OR interim) so a
      // mid-sentence pause never dispatches a partial command.
      clearDebounce();
      debounceTimer = setTimer(function () {
        debounceTimer = null;
        flush();
      }, DEBOUNCE_MS);
    }

    /** Auto-keepalive: restart 150ms after onend, only while still listening. */
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

  var BQVoiceCommander = { createVoiceCommander: createVoiceCommander };
  if (typeof window !== 'undefined') window.BQVoiceCommander = BQVoiceCommander;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQVoiceCommander;
  }
})();
