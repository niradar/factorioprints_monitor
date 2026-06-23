/* App-shell behaviour. All progressive enhancement - the page works without it. */
(function () {
  'use strict';

  // --- theme toggle (persisted; applied pre-paint by an inline script in <head>)
  window.toggleTheme = function () {
    var root = document.documentElement;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('fpm-theme', next); } catch (e) {}
  };

  // --- settings: dim the email/test controls when alerts are off
  window.toggleAlerts = function (cb) {
    document.querySelectorAll('.cond').forEach(function (el) { el.classList.toggle('off', !cb.checked); });
  };

  // --- settings: copy a code block's command
  window.copyCode = function (btn) {
    var code = btn.closest('.codeblock').querySelector('.code').textContent;
    if (navigator.clipboard) navigator.clipboard.writeText(code);
    var prev = btn.innerHTML;
    btn.textContent = '✓ Copied';
    setTimeout(function () { btn.innerHTML = prev; }, 1400);
  };

  // --- user switcher popover
  window.toggleSwitcher = function (e) {
    e.stopPropagation();
    e.currentTarget.closest('.userbox').classList.toggle('open');
  };
  document.addEventListener('click', function (e) {
    document.querySelectorAll('.userbox.open').forEach(function (box) {
      if (!box.contains(e.target)) box.classList.remove('open');
    });
  });

  // --- whole-row navigation for tables (e.g. Blueprints list)
  document.addEventListener('click', function (e) {
    var row = e.target.closest('tr[data-href]');
    if (row && !e.target.closest('a, button, form, input, select')) {
      window.location.href = row.dataset.href;
    }
  });

  // --- live "awaiting reply" counters: nudge every counter on the page by a
  // delta without a reload. Marking a comment done lowers each awaiting count
  // that includes it (nav badge, inbox header, a blueprint's own count) by 1;
  // un-marking raises them. Elements opt in with class .js-awaiting; those
  // tagged data-hide-when-zero disappear at 0, and anything marked
  // data-show-when-awaiting follows the user-level badge.
  function awaitingValue(el) {
    // the counter is the element itself, or a .js-awaiting descendant of it
    var n = el.querySelector('.js-awaiting') || el;
    return Math.max(0, parseInt(n.textContent, 10) || 0);
  }
  function applyAwaitingDelta(delta) {
    document.querySelectorAll('.js-awaiting').forEach(function (el) {
      el.textContent = Math.max(0, (parseInt(el.textContent, 10) || 0) + delta);
    });
    // counters (or their wrappers) that vanish at zero
    document.querySelectorAll('[data-hide-when-zero]').forEach(function (el) {
      el.hidden = awaitingValue(el) === 0;
    });
    // chrome that follows the user-level badge (e.g. inbox "Mark all done")
    var badge = document.getElementById('nav-awaiting');
    if (badge) {
      var any = awaitingValue(badge) > 0;
      document.querySelectorAll('[data-show-when-awaiting]').forEach(function (el) {
        el.hidden = !any;
      });
    }
  }

  // --- Done toggle: upgrade the plain form POST to an in-place fetch
  document.addEventListener('submit', function (e) {
    var form = e.target.closest('.done-form');
    if (!form) return;
    e.preventDefault();

    var btn = form.querySelector('.ibtn.toggle');
    var row = form.closest('.crow');
    var list = form.closest('.listwrap');
    var status = list ? list.dataset.status : 'all';

    fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'fetch', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value },
      body: new FormData(form),
    })
      .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); })
      .then(function (data) {
        var on = data.handled;
        btn.setAttribute('aria-pressed', String(on));
        btn.querySelector('.lab').textContent = on ? 'Done' : 'Mark done';
        row.classList.toggle('is-done', on);
        applyAwaitingDelta(on ? -1 : 1);
        // In a filtered view the row no longer belongs - fade it out.
        if (status && status !== 'all') {
          row.style.transition = 'opacity .2s ease';
          row.style.opacity = '0';
          setTimeout(function () { row.remove(); }, 200);
        }
      })
      .catch(function () { form.submit(); }); // fall back to a normal POST
  });
})();
