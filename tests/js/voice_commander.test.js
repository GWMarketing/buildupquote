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
