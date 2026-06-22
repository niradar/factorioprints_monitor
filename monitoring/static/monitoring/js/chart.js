/* Tiny dependency-free line chart for the blueprint detail page.
   Reads #chart-data (JSON [{t: ms, fav, com}, ...]), renders an SVG into
   #favchart, and wires the metric/range toggles. Plots by real time so scan
   gaps show as spacing; segments spanning a gap are dashed. */
(function () {
  'use strict';
  var dataEl = document.getElementById('chart-data');
  var host = document.getElementById('favchart');
  if (!dataEl || !host) return;

  var SERIES = JSON.parse(dataEl.textContent);
  var note = document.getElementById('chart-note');
  var state = { metric: 'fav', range: 'all' };

  var W = 1000, H = 220, PADL = 56, PADR = 16, PADT = 18, PADB = 34;
  var X0 = PADL, X1 = W - PADR, Y0 = PADT, Y1 = H - PADB;

  function fmtDate(ms) {
    return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function ticks(min, max, n) {
    if (min === max) { min -= 1; max += 1; }
    var out = [];
    for (var i = 0; i < n; i++) out.push(min + (max - min) * i / (n - 1));
    return out;
  }
  function median(a) {
    if (!a.length) return 0;
    var s = a.slice().sort(function (x, y) { return x - y; });
    var m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  function render() {
    var key = state.metric;
    var data = SERIES.map(function (p) { return { t: p.t, v: p[key] }; });
    if (state.range !== 'all' && data.length) {
      var cutoff = data[data.length - 1].t - parseInt(state.range, 10) * 86400000;
      data = data.filter(function (p) { return p.t >= cutoff; });
    }
    if (!data.length) { host.innerHTML = '<div class="chart-empty">No data in this range.</div>'; if (note) note.textContent = ''; return; }

    var vals = data.map(function (p) { return p.v; });
    var vmin = Math.min.apply(null, vals), vmax = Math.max.apply(null, vals);
    var pad = (vmax - vmin) * 0.15 || Math.max(1, Math.round(vmax * 0.05)) || 1;
    var lo = Math.floor(vmin - pad), hi = Math.ceil(vmax + pad);
    if (lo < 0 && vmin >= 0) lo = 0;

    var tmin = data[0].t, tmax = data[data.length - 1].t;
    function sx(t) { return tmax === tmin ? (X0 + X1) / 2 : X0 + (t - tmin) / (tmax - tmin) * (X1 - X0); }
    function sy(v) { return Y1 - (v - lo) / (hi - lo) * (Y1 - Y0); }

    var svg = ['<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" role="img">'];

    ticks(lo, hi, 4).forEach(function (tv) {
      var y = sy(tv);
      svg.push('<line class="grid" x1="' + X0 + '" y1="' + y.toFixed(1) + '" x2="' + X1 + '" y2="' + y.toFixed(1) + '"/>');
      svg.push('<text class="axis" x="' + (X0 - 8) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end">' + Math.round(tv) + '</text>');
    });

    var pts = data.map(function (p) { return { x: sx(p.t), y: sy(p.v), t: p.t, v: p.v }; });

    if (pts.length > 1) {
      var area = 'M' + pts.map(function (p) { return p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' L');
      area += ' L' + pts[pts.length - 1].x.toFixed(1) + ',' + Y1 + ' L' + pts[0].x.toFixed(1) + ',' + Y1 + ' Z';
      svg.push('<path class="area" d="' + area + '"/>');
    }

    var dts = [];
    for (var i = 1; i < data.length; i++) dts.push(data[i].t - data[i - 1].t);
    var med = median(dts), hasGap = false;
    for (var j = 1; j < pts.length; j++) {
      var gap = med > 0 && (data[j].t - data[j - 1].t) > med * 2;
      if (gap) hasGap = true;
      svg.push('<path class="line' + (gap ? ' gap' : '') + '" d="M' + pts[j - 1].x.toFixed(1) + ',' + pts[j - 1].y.toFixed(1) +
               ' L' + pts[j].x.toFixed(1) + ',' + pts[j].y.toFixed(1) + '"/>');
    }

    pts.forEach(function (p, idx) {
      var last = idx === pts.length - 1;
      svg.push('<circle class="pt' + (last ? ' last' : '') + '" cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) +
               '" r="' + (last ? 5 : 4) + '"><title>' + fmtDate(p.t) + ': ' + p.v + '</title></circle>');
    });

    var idxs = pts.length === 1 ? [0] : [0, Math.floor((pts.length - 1) / 2), pts.length - 1];
    idxs.forEach(function (k) {
      svg.push('<text class="axis" x="' + pts[k].x.toFixed(1) + '" y="' + (H - 12) + '" text-anchor="middle">' + fmtDate(pts[k].t) + '</text>');
    });

    svg.push('</svg>');
    host.innerHTML = svg.join('');
    if (note) {
      note.innerHTML = hasGap
        ? 'Each point is a snapshot. <b>Dashed</b> = a stretch with no snapshots (slope interpolated).'
        : 'Each point is a snapshot.';
    }
  }

  function wireSeg(id, prop) {
    var seg = document.getElementById(id);
    if (!seg) return;
    seg.addEventListener('click', function (e) {
      var btn = e.target.closest('button');
      if (!btn) return;
      state[prop] = btn.dataset[prop];
      Array.prototype.forEach.call(seg.querySelectorAll('button'), function (b) {
        b.setAttribute('aria-pressed', String(b === btn));
      });
      render();
    });
  }
  wireSeg('metric-seg', 'metric');
  wireSeg('range-seg', 'range');
  render();
})();
