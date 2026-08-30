'use strict';

const { test } = require('node:test');
const assert = require('node:assert');

const { generateAudioFilename, downloadBlob } = require('../../app/static/js/audio_filename.js');

function today() {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  const yy = String(now.getFullYear()).slice(-2);
  return `${mm}_${dd}_${yy}`;
}

test('default name: <Client>_audioquote_<MM_DD_YY>.webm', () => {
  assert.strictEqual(
    generateAudioFilename('Brandon', null, 'audio/webm'),
    `Brandon_audioquote_${today()}.webm`);
});

test('m4a/mp4 recordings use the .m4a extension', () => {
  assert.strictEqual(
    generateAudioFilename('Brandon', null, 'audio/mp4'),
    `Brandon_audioquote_${today()}.m4a`);
  assert.strictEqual(
    generateAudioFilename('Brandon', null, 'audio/x-m4a'),
    `Brandon_audioquote_${today()}.m4a`);
  // Unknown/empty types default to webm.
  assert.strictEqual(
    generateAudioFilename('Brandon', null, ''),
    `Brandon_audioquote_${today()}.webm`);
});

test('missing client name defaults to "Client"', () => {
  assert.strictEqual(
    generateAudioFilename('', null, 'audio/webm'),
    `Client_audioquote_${today()}.webm`);
  assert.strictEqual(
    generateAudioFilename(null, null, 'audio/webm'),
    `Client_audioquote_${today()}.webm`);
});

test('client names are sanitized and collapsed', () => {
  assert.strictEqual(
    generateAudioFilename("John O'Brien", null, 'audio/webm'),
    `John_O_Brien_audioquote_${today()}.webm`);
  assert.strictEqual(
    generateAudioFilename('Bob  Smith   Jr.', null, 'audio/webm'),
    `Bob_Smith_Jr_audioquote_${today()}.webm`);
  assert.strictEqual(
    generateAudioFilename('  Acme Roofing  ', null, 'audio/webm'),
    `Acme_Roofing_audioquote_${today()}.webm`);
});

test('custom override wins and is sanitized', () => {
  assert.strictEqual(generateAudioFilename('Brandon', 'My Clip', 'audio/webm'), 'My_Clip.webm');
  assert.strictEqual(generateAudioFilename('Brandon', 'a/b:c*d', 'audio/webm'), 'a_b_c_d.webm');
  assert.strictEqual(generateAudioFilename('Brandon', 'My_Clip.webm', 'audio/webm'), 'My_Clip.webm');
  assert.strictEqual(generateAudioFilename('Brandon', 'My_Clip.m4a', 'audio/mp4'), 'My_Clip.m4a');
  // Custom name with wrong/absent extension gets the recording extension.
  assert.strictEqual(generateAudioFilename('Brandon', 'My_Clip.txt', 'audio/webm'), 'My_Clip_txt.webm');
  assert.strictEqual(generateAudioFilename('Brandon', 'My Clip', 'audio/mp4'), 'My_Clip.m4a');
});

test('custom override ignores the client name entirely', () => {
  const name = generateAudioFilename('Ignored Client', 'Session A', 'audio/webm');
  assert.strictEqual(name, 'Session_A.webm');
  assert.ok(!name.includes('Ignored'));
});

test('whitespace-only custom name falls back to the structured name', () => {
  assert.strictEqual(
    generateAudioFilename('Brandon', '   ', 'audio/webm'),
    `Brandon_audioquote_${today()}.webm`);
});

test('downloadBlob returns false outside a browser', () => {
  // Node has URL but no createObjectURL / document -> graceful false.
  assert.strictEqual(downloadBlob(new Blob([]), 'x.webm'), false);
});
