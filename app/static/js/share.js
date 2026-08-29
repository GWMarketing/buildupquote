/* One-click proposal sharing: WhatsApp, SMS, and copy-link.
 *
 * Loaded on every page via base.html (after app.js) as the global BQShare.
 * The pure helpers (sanitizePhone / buildMessage / buildLinks) have no DOM
 * dependencies so they can be unit-tested under Node (tests/js/share.test.js).
 *
 * Every share action fires two window events so the app can react/log:
 *   PROPOSAL_SHARED_VIA_WHATSAPP / _VIA_SMS / _VIA_COPY   (detail: {method, quoteId, at})
 *   PROPOSAL_SHARED                                        (same detail)
 */
(function () {
  'use strict';

  /** Strip spaces, dashes, brackets and any other non-numeric characters,
   *  keeping a single leading '+' (for international dialling). */
  function sanitizePhone(phone) {
    if (!phone) return '';
    const cleaned = String(phone).trim();
    const hasPlus = cleaned.charAt(0) === '+';
    const digits = cleaned.replace(/[^0-9]/g, '');
    return (hasPlus ? '+' : '') + digits;
  }

  /** The standard share message. */
  function buildMessage(opts) {
    const { clientName, projectName, quoteUrl } = opts || {};
    const who = (clientName && String(clientName).trim()) || 'there';
    const project = (projectName && String(projectName).trim()) || 'this project';
    return 'Hi ' + who + ', here is your proposal for "' + project +
      '". Review, approve, and sign online here: ' + (quoteUrl || '');
  }

  /** Platform deep links for a share action. */
  function buildLinks(opts) {
    const { clientName, projectName, quoteUrl, clientPhone } = opts || {};
    const cleanPhone = sanitizePhone(clientPhone);
    const message = buildMessage({ clientName: clientName, projectName: projectName, quoteUrl: quoteUrl });
    const encoded = encodeURIComponent(message);

    let whatsapp;
    if (cleanPhone) {
      whatsapp = 'https://wa.me/' + cleanPhone + '?text=' + encoded;
    } else {
      // No phone on file: fall back to a share-without-number WhatsApp link.
      whatsapp = 'https://api.whatsapp.com/send?text=' + encoded;
    }
    const sms = 'sms:' + cleanPhone + '?&body=' + encoded;
    return { whatsapp: whatsapp, sms: sms, message: message, phone: cleanPhone };
  }

  /** Dispatch the activity events for a share action. */
  function track(method, quoteId) {
    const name = 'PROPOSAL_SHARED_VIA_' + String(method).toUpperCase();
    const detail = {
      method: String(method).toLowerCase(),
      quoteId: quoteId || null,
      at: new Date().toISOString(),
    };
    if (typeof window !== 'undefined' && window.dispatchEvent) {
      window.dispatchEvent(new CustomEvent(name, { detail: detail }));
      window.dispatchEvent(new CustomEvent('PROPOSAL_SHARED', { detail: detail }));
    }
    if (typeof console !== 'undefined') {
      console.debug('[share]', name, detail);
    }
    return detail;
  }

  function openLink(href) {
    if (typeof window !== 'undefined') {
      window.open(href, '_blank', 'noopener,noreferrer');
    }
  }

  function copyText(text) {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    // Fallback for older browsers / non-secure contexts.
    return new Promise(function (resolve, reject) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        resolve();
      } catch (e) { reject(e); }
    });
  }

  /** Google Maps directions deep link for a site address (no API key). */
  function googleMapsUrl(address) {
    return 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(address || '');
  }

  /** Zillow deep link for an address (the contractor's property-value check). */
  function zillowUrl(address) {
    const slug = String(address || '')
      .replace(/[^a-z0-9]+/gi, '-')
      .replace(/^-+|-+$/g, '');
    return slug ? 'https://www.zillow.com/homes/' + slug + '_rb/' : 'https://www.zillow.com/homes/';
  }

  const BQShare = {
    sanitizePhone: sanitizePhone,
    buildMessage: buildMessage,
    buildLinks: buildLinks,
    track: track,
    openLink: openLink,
    copyText: copyText,
    googleMapsUrl: googleMapsUrl,
    zillowUrl: zillowUrl,
  };

  if (typeof window !== 'undefined') window.BQShare = BQShare;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQShare;
  }
})();
