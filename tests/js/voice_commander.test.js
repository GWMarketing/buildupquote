'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { createVoiceCommander } = require('../../app/static/js/voice_commander.js');

function finalResult(text) { return { isFinal: true, 0: { transcript: text } }; }
function interimResult(text) { return { isFinal: false, 0: { transcript: text } }; }
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

test('finals accumulate across pauses and dispatch once after silence', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 30 });
  c.start();
  c.onResult([finalResult('add fifteen gallons of paint')]);
  c.onResult([finalResult('at eighteen bucks a piece')]);
  assert.strictEqual(commands.length, 0);
  assert.strictEqual(c.getBuffer(), 'add fifteen gallons of paint at eighteen bucks a piece');
  await sleep(60);
  assert.strictEqual(commands.length, 1);
  assert.strictEqual(commands[0], 'add fifteen gallons of paint at eighteen bucks a piece');
  assert.strictEqual(c.getBuffer(), '');
});

test('interim speech resets the silence window (no premature dispatch)', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 60 });
  c.start();
  c.onResult([finalResult('fifteen gallons')]);
  await sleep(40);                        // approaching the window...
  c.onResult([interimResult(' of paint at')]);  // ...but more speech arrives
  await sleep(40);                        // still inside the new window
  assert.strictEqual(commands.length, 0);
  await sleep(40);                        // 60ms after the last token
  assert.strictEqual(commands.length, 1);
  assert.strictEqual(commands[0], 'fifteen gallons');
});

test('termination phrase flushes immediately', () => {
  const commands = [];
  const c = createVoiceCommander({
    onCommand: t => commands.push(t),
    isTermination: t => /mic off/.test(t),
  });
  c.start();
  c.onResult([finalResult('mic off')]);
  assert.strictEqual(commands.length, 1);
  assert.strictEqual(commands[0], 'mic off');
  assert.strictEqual(c.getBuffer(), '');
});

test('interim transcript surfaces via onStatus', () => {
  const statuses = [];
  const c = createVoiceCommander({ onStatus: s => statuses.push(s) });
  c.start();
  c.onResult([interimResult('drywall 12 by')]);
  assert.strictEqual(statuses[statuses.length - 1].interim, 'drywall 12 by');
  c.onResult([finalResult('drywall 12 by 14')]);
  assert.strictEqual(statuses[statuses.length - 1].interim, 'drywall 12 by 14');
});

test('stop cancels pending dispatch and clears the buffer', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 30 });
  c.start();
  c.onResult([finalResult('blue paint primer')]);
  c.stop();
  assert.strictEqual(c.getBuffer(), '');
  assert.strictEqual(c.isListening(), false);
  await sleep(60);
  assert.strictEqual(commands.length, 0);
});

test('onEnd schedules the restart only while listening', async () => {
  let restarts = 0;
  const c = createVoiceCommander({ onRestart: () => restarts++, restartMs: 20 });
  c.start();
  c.onEnd();
  assert.strictEqual(restarts, 0);
  await sleep(40);
  assert.strictEqual(restarts, 1);
  // After stop, onend must never restart.
  c.stop();
  c.onEnd();
  await sleep(40);
  assert.strictEqual(restarts, 1);
});

test('onResult before start and after stop is ignored', () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 10 });
  c.onResult([finalResult('ignored before start')]);
  c.start();
  c.stop();
  c.onResult([finalResult('ignored after stop')]);
  assert.strictEqual(c.getBuffer(), '');
  assert.strictEqual(commands.length, 0);
});

test('flush dispatches and clears the buffer immediately', () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t) });
  c.start();
  c.onResult([finalResult('site address is 1400 Mockingbird')]);
  c.flush();
  assert.strictEqual(commands.length, 1);
  assert.strictEqual(c.getBuffer(), '');
});

// ---- Background-noise resilience -------------------------------------------

test('low-confidence noise finals never reach the dispatcher', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 30 });
  c.start();
  c.onResult([{ isFinal: true, 0: { transcript: 'um like background noise', confidence: 0.05 } }]);
  c.onResult([{ isFinal: true, 0: { transcript: 'add fifteen gallons of paint', confidence: 0.85 } }]);
  await sleep(60);
  assert.strictEqual(commands.length, 1);
  assert.strictEqual(commands[0], 'add fifteen gallons of paint');
});

test('junk-word finals are dropped even at high confidence', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 30 });
  c.start();
  c.onResult([{ isFinal: true, 0: { transcript: 'the and uh', confidence: 0.95 } }]);
  c.onResult([{ isFinal: true, 0: { transcript: 'you know like', confidence: 0.9 } }]);
  await sleep(60);
  assert.strictEqual(commands.length, 0);
  assert.strictEqual(c.getBuffer(), '');
});

test('consecutive identical finals are deduped into one', async () => {
  const commands = [];
  const c = createVoiceCommander({ onCommand: t => commands.push(t), debounceMs: 30 });
  c.start();
  c.onResult([{ isFinal: true, 0: { transcript: 'background noise', confidence: 0.6 } }]);
  c.onResult([{ isFinal: true, 0: { transcript: 'background noise', confidence: 0.6 } }]);
  assert.strictEqual(c.getBuffer(), 'background noise');
  await sleep(60);
  assert.strictEqual(commands.length, 1);
});

test('the same command within cooldown is not re-dispatched', async () => {
  const commands = [];
  const c = createVoiceCommander({
    onCommand: t => commands.push(t), debounceMs: 20, sameCommandCooldownMs: 5000,
  });
  c.start();
  c.onResult([{ isFinal: true, 0: { transcript: 'address is 1400 mockinbird lane', confidence: 0.9 } }]);
  await sleep(40);
  assert.strictEqual(commands.length, 1);
  // A noisy echo of the exact same phrase must not re-fire within the cooldown.
  c.onResult([{ isFinal: true, 0: { transcript: 'address is 1400 mockinbird lane', confidence: 0.9 } }]);
  await sleep(40);
  assert.strictEqual(commands.length, 1);
  // A different command dispatches normally (after the debounce — this
  // commander has no isTermination handler, so "mic off" is just a phrase).
  c.onResult([{ isFinal: true, 0: { transcript: 'mic off', confidence: 0.9 } }]);
  await sleep(40);
  assert.strictEqual(commands.length, 2);
  assert.strictEqual(commands[1], 'mic off');
});

test('junk interims do not surface in the live pill', () => {
  const statuses = [];
  const c = createVoiceCommander({ onStatus: s => statuses.push(s) });
  c.start();
  c.onResult([interimResult('um like')]);              // junk -> suppressed
  assert.strictEqual(statuses[statuses.length - 1].interim, '');
  c.onResult([interimResult('drywall 12 by')]);        // real -> shown
  assert.strictEqual(statuses[statuses.length - 1].interim, 'drywall 12 by');
});

test('defaults are tuned for noise: 1800ms debounce, 0.3 confidence', () => {
  const mod = require('../../app/static/js/voice_commander.js');
  assert.strictEqual(mod.DEFAULTS.debounceMs, 1800);
  assert.strictEqual(mod.DEFAULTS.restartMs, 150);
  assert.strictEqual(mod.DEFAULTS.minConfidence, 0.3);
  assert.strictEqual(mod.DEFAULTS.sameCommandCooldownMs, 2500);
  assert.strictEqual(mod.isMeaningful('mic off'), true);
  assert.strictEqual(mod.isMeaningful('address is 1400 mockinbird lane'), true);
  assert.strictEqual(mod.isMeaningful('um like you know'), false);
  assert.strictEqual(mod.isMeaningful('the and uh'), false);
  assert.strictEqual(mod.isMeaningful(''), false);
});

