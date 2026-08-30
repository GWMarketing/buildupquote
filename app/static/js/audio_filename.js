/* Structured local-audio filename generator + browser download helper.
 *
 * generateAudioFilename(clientName, customName, mimeType):
 *   "Brandon_audioquote_08_30_26.webm"  (default, from the recording type)
 *   "Brandon_audioquote_08_30_26.m4a"   (mp4/m4a recordings)
 *   "My_Clip.webm"                      (custom override, sanitized)
 *
 * Pure functions, no DOM deps: exposed as window.BQAudioFilename and as a
 * CommonJS module for Node tests (tests/js/audio_filename.test.js). The
 * builder records the voice session with MediaRecorder and downloads the blob
 * through downloadBlob() using this generated name.
 */
(function () {
  'use strict';

  function generateAudioFilename(clientName, customName, mimeType) {
    var mt = String(mimeType || 'audio/webm').toLowerCase();
    var extension = (mt.indexOf('mp4') !== -1 || mt.indexOf('m4a') !== -1) ? 'm4a' : 'webm';

    // Explicit override: sanitize to a safe file-name charset and ensure an
    // audio extension. The extension is detected BEFORE sanitizing so dots in
    // "My_Clip.webm" are preserved (sanitizing first would turn it into
    // "My_Clip_webm" and double-append).
    if (customName && String(customName).trim()) {
      var rawCustom = String(customName).trim();
      var extMatch = rawCustom.match(/\.([A-Za-z0-9]{1,5})$/);
      if (extMatch && /^(m4a|webm|mp4|mp3|wav|ogg)$/i.test(extMatch[1])) {
        var base = rawCustom.slice(0, rawCustom.length - extMatch[0].length)
          .replace(/[^a-zA-Z0-9_-]/g, '_');
        return base + '.' + extMatch[1].toLowerCase();
      }
      var cleanCustom = rawCustom.replace(/[^a-zA-Z0-9_-]/g, '_');
      return cleanCustom + '.' + extension;
    }

    // 1. Sanitize client name (spaces/special chars -> '_', collapse runs,
    //    trim stray edge underscores so the "_audioquote_" join stays clean).
    var formattedClient = (clientName && String(clientName).trim())
      ? String(clientName).trim().replace(/[^a-zA-Z0-9]/g, '_')
        .replace(/_+/g, '_').replace(/^_+|_+$/g, '')
      : 'Client';

    // 2. Date as MM_DD_YY.
    var now = new Date();
    var mm = String(now.getMonth() + 1).padStart(2, '0');
    var dd = String(now.getDate()).padStart(2, '0');
    var yy = String(now.getFullYear()).slice(-2);

    // 3. <ClientName>_audioquote_<MM_DD_YY>.<ext>
    return formattedClient + '_audioquote_' + mm + '_' + dd + '_' + yy + '.' + extension;
  }

  /** Trigger a browser save-as for a recording blob. Returns false when the
   *  DOM/URL APIs are unavailable (e.g. under Node). */
  function downloadBlob(blob, filename) {
    if (typeof document === 'undefined' || typeof URL === 'undefined' ||
        !URL.createObjectURL || !document.createElement) return false;
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    return true;
  }

  var BQAudioFilename = {
    generateAudioFilename: generateAudioFilename,
    downloadBlob: downloadBlob,
  };
  if (typeof window !== 'undefined') window.BQAudioFilename = BQAudioFilename;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BQAudioFilename;
  }
})();
