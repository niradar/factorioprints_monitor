/* App-shell behaviour. All progressive enhancement — the page works without it. */
(function () {
  'use strict';

  // --- theme toggle (persisted; applied pre-paint by an inline script in <head>)
  window.toggleTheme = function () {
    var root = document.documentElement;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('fpm-theme', next); } catch (e) {}
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
        // In a filtered view the row no longer belongs — fade it out.
        if (status && status !== 'all') {
          row.style.transition = 'opacity .2s ease';
          row.style.opacity = '0';
          setTimeout(function () { row.remove(); }, 200);
        }
      })
      .catch(function () { form.submit(); }); // fall back to a normal POST
  });
})();
