/* Snapshot: AJAX-trigger + live status poller (Tier A).
   Triggering fires a fetch (no navigation); while a snapshot runs we poll a tiny
   JSON status endpoint and show elapsed time in place. When it stops, we reload
   once so the server re-renders the new data + final state (last scan / failure /
   cooldown). Works as a plain form POST if JavaScript is off. */
(function () {
  'use strict';
  var form = document.getElementById('snap-form');
  var btn = document.getElementById('snap-btn');
  var meta = document.getElementById('scanmeta');
  if (!form || !meta) return;

  var statusUrl = form.dataset.statusUrl;
  var startedAt = meta.dataset.started ? new Date(meta.dataset.started).getTime() : Date.now();
  var pollTimer = null, tickTimer = null;

  function fmtElapsed(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function tick() {
    var el = document.getElementById('scan-elapsed');
    if (el) el.textContent = fmtElapsed(Date.now() - startedAt);
  }
  // lock every snapshot trigger (topbar button + any in-page CTAs) so a scan
  // can't be double-started while one is already kicking off
  function lockTriggers() {
    if (btn) btn.disabled = true;
    document.querySelectorAll('.js-snap-trigger').forEach(function (el) { el.disabled = true; });
  }

  function showScanning() {
    meta.dataset.running = '1';
    meta.innerHTML = '⏳ <b>scanning…</b><br><span id="scan-elapsed"></span>';
    lockTriggers();
    tick();
  }

  function poll() {
    fetch(statusUrl, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.running) {
          if (d.started_at) startedAt = new Date(d.started_at).getTime();
          tick();
        } else {
          stop();
          window.location.reload();  // bring in the new data + final state
        }
      })
      .catch(function () { /* transient error - keep polling */ });
  }

  function start() {
    if (pollTimer) return;
    tickTimer = setInterval(tick, 1000);
    pollTimer = setInterval(poll, 3000);
    poll();
  }
  function stop() {
    clearInterval(pollTimer); clearInterval(tickTimer);
    pollTimer = tickTimer = null;
  }

  // pause polling while the tab is hidden; resume when visible again
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else if (meta.dataset.running === '1') start();
  });

  form.addEventListener('submit', function (e) {
    if (btn && btn.disabled) { e.preventDefault(); return; }
    e.preventDefault();
    lockTriggers();  // close the double-click window during the fetch round-trip
    fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'fetch', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value },
      body: new FormData(form),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.started || d.running) { startedAt = Date.now(); showScanning(); start(); }
      })
      .catch(function () { form.submit(); });  // fall back to a normal POST
  });

  // if a run is already in progress on load, start tracking it
  if (meta.dataset.running === '1') start();
})();
