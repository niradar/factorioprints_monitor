/* Client-side sort + pagination for the Blueprints table.
   The whole table is rendered once; sorting and paging happen in the browser
   with no round-trip. Header <a> links remain a no-JS fallback (server sort).
   Cells carry data-sort values (numbers / epoch seconds) for typed sorting. */
(function () {
  'use strict';
  var table = document.querySelector('table.bp');
  var pagerEl = document.getElementById('bp-pager');
  if (!table || !pagerEl) return;

  var tbody = table.tBodies[0];
  var headers = Array.prototype.slice.call(table.querySelectorAll('thead th'));
  var allRows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
  var PER_OPTIONS = [10, 25, 50];
  // Pick up the server's initial sort (column index + direction) so the window
  // switcher's "sort by Δ" survives the client-side takeover. Falls back to the
  // last column (last comment), newest first.
  var initCol = parseInt(table.getAttribute('data-sort-col'), 10);
  if (isNaN(initCol)) initCol = headers.length - 1;
  var state = { sort: initCol, asc: table.getAttribute('data-sort-asc') === '1', page: 1, per: 10 };

  function value(row, idx, type) {
    var td = row.children[idx];
    var raw = td.getAttribute('data-sort');
    if (raw == null) raw = td.textContent;
    if (type === 'num' || type === 'date') return parseFloat(raw) || 0;
    return raw.trim().toLowerCase();
  }

  function sortRows() {
    if (state.sort < 0) return;
    var type = headers[state.sort].getAttribute('data-type') || 'str';
    allRows.sort(function (a, b) {
      var x = value(a, state.sort, type), y = value(b, state.sort, type);
      return (x < y ? -1 : x > y ? 1 : 0) * (state.asc ? 1 : -1);
    });
  }

  function windowed(pageCount, cur) {
    var keep = {};
    [1, 2, cur - 1, cur, cur + 1, pageCount - 1, pageCount].forEach(function (p) {
      if (p >= 1 && p <= pageCount) keep[p] = true;
    });
    var out = [], prev = 0;
    Object.keys(keep).map(Number).sort(function (a, b) { return a - b; }).forEach(function (p) {
      if (p - prev > 1) out.push('…');
      out.push(p);
      prev = p;
    });
    return out;
  }

  function renderPager(total, start, end, pageCount) {
    var opts = PER_OPTIONS.map(function (o) {
      return '<option value="' + o + '"' + (o === state.per ? ' selected' : '') + '>' + o + '</option>';
    }).join('');
    var pages = windowed(pageCount, state.page).map(function (p) {
      if (p === '…') return '<a class="dis">…</a>';
      if (p === state.page) return '<a class="cur">' + p + '</a>';
      return '<a data-page="' + p + '">' + p + '</a>';
    }).join('');
    var prev = state.page > 1 ? '<a data-page="' + (state.page - 1) + '">‹</a>' : '<a class="dis">‹</a>';
    var next = state.page < pageCount ? '<a data-page="' + (state.page + 1) + '">›</a>' : '<a class="dis">›</a>';

    pagerEl.className = 'pager';
    pagerEl.innerHTML =
      '<span class="left"><span class="mono">Showing ' + (total ? start + 1 : 0) + '–' + end + ' of ' + total + '</span>' +
      '<span class="perpage">Per page <span class="ppsel"><select id="bp-per">' + opts + '</select>' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg></span></span></span>' +
      '<span class="pages mono">' + prev + pages + next + '</span>';

    pagerEl.querySelector('#bp-per').addEventListener('change', function () {
      state.per = parseInt(this.value, 10); state.page = 1; render();
    });
    Array.prototype.forEach.call(pagerEl.querySelectorAll('a[data-page]'), function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        state.page = parseInt(a.getAttribute('data-page'), 10);
        render();
        table.scrollIntoView({ block: 'nearest' });
      });
    });
  }

  function render() {
    sortRows();
    var total = allRows.length;
    var pageCount = Math.max(1, Math.ceil(total / state.per));
    if (state.page > pageCount) state.page = pageCount;
    var start = (state.page - 1) * state.per;
    var end = Math.min(start + state.per, total);

    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
    for (var i = start; i < end; i++) tbody.appendChild(allRows[i]);

    headers.forEach(function (h) { var a = h.querySelector('.arr'); if (a) a.remove(); });
    if (state.sort >= 0) {
      (headers[state.sort].querySelector('a') || headers[state.sort])
        .insertAdjacentHTML('beforeend', ' <span class="arr">' + (state.asc ? '▲' : '▼') + '</span>');
    }
    renderPager(total, start, end, pageCount);
  }

  headers.forEach(function (th, idx) {
    var trigger = th.querySelector('a') || th;
    th.style.cursor = 'pointer';
    trigger.addEventListener('click', function (e) {
      e.preventDefault();
      var type = th.getAttribute('data-type') || 'str';
      if (state.sort === idx) state.asc = !state.asc;
      else { state.sort = idx; state.asc = (type === 'str'); }
      state.page = 1;
      render();
    });
  });

  render();
})();
