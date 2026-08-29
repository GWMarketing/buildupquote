'use strict';

const test = require('node:test');
const assert = require('node:assert');

const BQShare = require('../../app/static/js/share.js');

test('sanitizePhone strips non-numeric chars but keeps a leading +', () => {
  assert.strictEqual(BQShare.sanitizePhone('+1 (555) 010-1234'), '+15550101234');
  assert.strictEqual(BQShare.sanitizePhone('(555) 010-1234'), '5550101234');
  assert.strictEqual(BQShare.sanitizePhone(' 447700 900123 '), '447700900123');
  assert.strictEqual(BQShare.sanitizePhone('+44 7700 900123'), '+447700900123');
  assert.strictEqual(BQShare.sanitizePhone(''), '');
  assert.strictEqual(BQShare.sanitizePhone(null), '');
  assert.strictEqual(BQShare.sanitizePhone(undefined), '');
});

test('buildMessage uses the exact share template', () => {
  const msg = BQShare.buildMessage({
    clientName: 'Joan',
    projectName: 'Kitchen Reno',
    quoteUrl: 'https://x.test/view/quote/abc',
  });
  assert.strictEqual(
    msg,
    'Hi Joan, here is your proposal for "Kitchen Reno". Review, approve, and sign online here: https://x.test/view/quote/abc',
  );
});

test('buildMessage falls back when client name is missing', () => {
  const msg = BQShare.buildMessage({
    projectName: 'Roof',
    quoteUrl: 'https://x.test/v',
  });
  assert.strictEqual(
    msg,
    'Hi there, here is your proposal for "Roof". Review, approve, and sign online here: https://x.test/v',
  );
});

test('buildLinks: WhatsApp + SMS deep links with a phone', () => {
  const links = BQShare.buildLinks({
    clientName: 'Joan',
    projectName: 'Kitchen',
    quoteUrl: 'https://x.test/view/quote/abc',
    clientPhone: '+1 (555) 010-1234',
  });
  assert.ok(links.whatsapp.startsWith('https://wa.me/+15550101234?text='), links.whatsapp);
  assert.ok(links.sms.startsWith('sms:+15550101234?&body='), links.sms);
  // The encoded message is the share template.
  assert.ok(decodeURIComponent(links.whatsapp).includes('Hi Joan'));
  assert.ok(decodeURIComponent(links.sms).includes('"Kitchen"'));
});

test('buildLinks: WhatsApp falls back without a phone', () => {
  const links = BQShare.buildLinks({
    clientName: 'Joan',
    projectName: 'Kitchen',
    quoteUrl: 'https://x.test/view/quote/abc',
  });
  assert.ok(links.whatsapp.startsWith('https://api.whatsapp.com/send?text='), links.whatsapp);
  assert.ok(links.sms.startsWith('sms:?&body='), links.sms);
});

test('buildLinks: URL is encoded into the message', () => {
  const links = BQShare.buildLinks({
    projectName: 'Basement',
    quoteUrl: 'https://x.test/view/quote/abc-123',
  });
  const decoded = decodeURIComponent(links.whatsapp);
  assert.ok(decoded.includes('https://x.test/view/quote/abc-123'));
});

test('track dispatches PROPOSAL_SHARED_VIA_<METHOD> events', () => {
  const seen = [];
  global.window = {
    dispatchEvent(evt) { seen.push(evt.type); },
  };
  try {
    const detail = BQShare.track('whatsapp', 42);
    assert.deepStrictEqual(seen, ['PROPOSAL_SHARED_VIA_WHATSAPP', 'PROPOSAL_SHARED']);
    assert.strictEqual(detail.method, 'whatsapp');
    assert.strictEqual(detail.quoteId, 42);
  } finally {
    delete global.window;
  }
});
