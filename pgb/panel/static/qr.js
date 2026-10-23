/* Minimal offline QR encoder: byte mode, EC level M, versions 1-10. Exposes window.QR.svg(text). */
(function (root) {
  "use strict";
  var EC = { // version: [ecPerBlock, [[count, dataCw], ...]]
    1: [10, [[1, 16]]], 2: [16, [[1, 28]]], 3: [26, [[1, 44]]], 4: [18, [[2, 32]]], 5: [24, [[2, 43]]],
    6: [16, [[4, 27]]], 7: [18, [[4, 31]]], 8: [22, [[2, 38], [2, 39]]], 9: [22, [[3, 36], [2, 37]]],
    10: [26, [[4, 43], [1, 44]]]
  };
  var ALIGN = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
    8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] };
  var EXP = new Array(512), LOG = new Array(256);
  (function () { var x = 1; for (var i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 256) x ^= 0x11d; }
    for (i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  function mul(a, b) { return a && b ? EXP[LOG[a] + LOG[b]] : 0; }
  function rsGen(n) { var g = [1]; for (var i = 0; i < n; i++) { var ng = new Array(g.length + 1).fill(0);
      for (var j = 0; j < g.length; j++) { ng[j] ^= g[j]; ng[j + 1] ^= mul(g[j], EXP[i]); } g = ng; } return g; }
  function rsRem(data, n) { var g = rsGen(n), r = new Array(n).fill(0);
    data.forEach(function (d) { var f = d ^ r.shift(); r.push(0); for (var i = 0; i < n; i++) r[i] ^= mul(g[i + 1], f); });
    return r; }
  function utf8(s) { return Array.prototype.slice.call(new TextEncoder().encode(s)); }

  function encode(text) {
    var bytes = utf8(text), ver = 0, total = 0;
    for (var v = 1; v <= 10; v++) { var cap = EC[v][1].reduce(function (a, b) { return a + b[0] * b[1]; }, 0);
      if (bytes.length + (v < 10 ? 2 : 3) <= cap) { ver = v; total = cap; break; } }
    if (!ver) throw new Error("text too long for QR");
    var bits = [];
    function put(val, len) { for (var i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); }
    put(4, 4); put(bytes.length, ver < 10 ? 8 : 16); bytes.forEach(function (b) { put(b, 8); });
    put(0, Math.min(4, total * 8 - bits.length)); while (bits.length % 8) bits.push(0);
    var data = []; for (var i = 0; i < bits.length; i += 8) { var b = 0; for (var k = 0; k < 8; k++) b = (b << 1) | bits[i + k]; data.push(b); }
    for (var pad = 0xEC; data.length < total; pad ^= 0xEC ^ 0x11) data.push(pad);
    var ecn = EC[ver][0], blocks = [], pos = 0;
    EC[ver][1].forEach(function (g) { for (var c = 0; c < g[0]; c++) { var d = data.slice(pos, pos + g[1]); pos += g[1]; blocks.push({ d: d, e: rsRem(d, ecn) }); } });
    var out = [], maxD = Math.max.apply(null, blocks.map(function (b) { return b.d.length; }));
    for (i = 0; i < maxD; i++) blocks.forEach(function (b) { if (i < b.d.length) out.push(b.d[i]); });
    for (i = 0; i < ecn; i++) blocks.forEach(function (b) { out.push(b.e[i]); });

    var n = ver * 4 + 17, m = [], fn = [];
    for (i = 0; i < n; i++) { m.push(new Array(n).fill(false)); fn.push(new Array(n).fill(false)); }
    function set(x, y, v) { m[y][x] = v; fn[y][x] = true; }
    function finder(cx, cy) { for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++) { var x = cx + dx, y = cy + dy;
        if (x < 0 || y < 0 || x >= n || y >= n) continue; var d = Math.max(Math.abs(dx), Math.abs(dy)); set(x, y, d !== 2 && d !== 4); } }
    finder(3, 3); finder(n - 4, 3); finder(3, n - 4);
    for (i = 8; i < n - 8; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    var al = ALIGN[ver];
    al.forEach(function (ay) { al.forEach(function (ax) {
      if ((ax === 6 && ay === 6) || (ax === 6 && ay === al[al.length - 1]) || (ay === 6 && ax === al[al.length - 1])) return;
      for (var dy = -2; dy <= 2; dy++) for (var dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1); }); });
    function bch(v, poly, deg) { var r = v << deg; var db = 32 - Math.clz32(poly); for (var j = 32 - Math.clz32(r); j >= db; j--) if ((r >>> (j - 1)) & 1) r ^= poly << (j - db); return r; }
    function formatBits(mask) { var d = (0 << 3) | mask; return ((d << 10) | bch(d, 0x537, 10)) ^ 0x5412; }
    function drawFormat(mask) { var f = formatBits(mask);
      for (var j = 0; j <= 5; j++) set(8, j, ((f >>> j) & 1) === 1);
      set(8, 7, ((f >>> 6) & 1) === 1); set(8, 8, ((f >>> 7) & 1) === 1); set(7, 8, ((f >>> 8) & 1) === 1);
      for (j = 9; j < 15; j++) set(14 - j, 8, ((f >>> j) & 1) === 1);
      for (j = 0; j < 8; j++) set(n - 1 - j, 8, ((f >>> j) & 1) === 1);
      for (j = 8; j < 15; j++) set(8, n - 15 + j, ((f >>> j) & 1) === 1);
      set(8, n - 8, true); }
    drawFormat(0);
    if (ver >= 7) { var vi = (ver << 12) | bch(ver, 0x1F25, 12);
      for (i = 0; i < 18; i++) { var bit = ((vi >>> i) & 1) === 1, a = n - 11 + (i % 3), b = Math.floor(i / 3); set(a, b, bit); set(b, a, bit); } }
    var dbits = []; out.forEach(function (c) { for (var j = 7; j >= 0; j--) dbits.push((c >>> j) & 1); });
    var idx = 0;
    for (var right = n - 1; right >= 1; right -= 2) { if (right === 6) right = 5;
      for (var vert = 0; vert < n; vert++) for (var j = 0; j < 2; j++) {
        var x = right - j, up = ((right + 1) & 2) === 0, y = up ? n - 1 - vert : vert;
        if (!fn[y][x] && idx < dbits.length) { m[y][x] = dbits[idx++] === 1; } } }
    var masks = [function (x, y) { return (x + y) % 2 === 0; }, function (x, y) { return y % 2 === 0; }, function (x, y) { return x % 3 === 0; },
      function (x, y) { return (x + y) % 3 === 0; }, function (x, y) { return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; },
      function (x, y) { return x * y % 2 + x * y % 3 === 0; }, function (x, y) { return (x * y % 2 + x * y % 3) % 2 === 0; },
      function (x, y) { return ((x + y) % 2 + x * y % 3) % 2 === 0; }];
    var best = null, bestScore = 1e9;
    for (var mk = 0; mk < 8; mk++) {
      var t = m.map(function (r) { return r.slice(); });
      for (y = 0; y < n; y++) for (x = 0; x < n; x++) if (!fn[y][x] && masks[mk](x, y)) t[y][x] = !t[y][x];
      var saved = m; m = t; drawFormat(mk); var sc = penalty(m, n); t = m; m = saved;
      if (sc < bestScore) { bestScore = sc; best = t; } }
    return best;
  }
  function penalty(m, n) {
    var s = 0, x, y, run, dark = 0;
    for (var pass = 0; pass < 2; pass++) for (y = 0; y < n; y++) { run = 1;
      for (x = 1; x < n; x++) { var a = pass ? m[x][y] : m[y][x], b = pass ? m[x - 1][y] : m[y][x - 1];
        if (a === b) { run++; if (run === 5) s += 3; else if (run > 5) s++; } else run = 1; } }
    for (y = 0; y < n - 1; y++) for (x = 0; x < n - 1; x++) { var c = m[y][x]; if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) s += 3; }
    var pat = [true, false, true, true, true, false, true, false, false, false, false], rpat = pat.slice().reverse();
    function chk(get) { for (var i = 0; i < n; i++) for (var j = 0; j + 11 <= n; j++) { var ok1 = true, ok2 = true;
        for (var k = 0; k < 11; k++) { var v = get(i, j + k); if (v !== pat[k]) ok1 = false; if (v !== rpat[k]) ok2 = false; } if (ok1) s += 40; if (ok2) s += 40; } }
    chk(function (i, j) { return m[i][j]; }); chk(function (i, j) { return m[j][i]; });
    for (y = 0; y < n; y++) for (x = 0; x < n; x++) if (m[y][x]) dark++;
    s += Math.floor(Math.abs(dark * 20 - n * n * 10) / (n * n)) * 10;
    return s;
  }
  function svg(text, px) {
    var m = encode(text), n = m.length, q = 4, d = "";
    for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) if (m[y][x]) d += "M" + (x + q) + " " + (y + q) + "h1v1h-1z";
    var size = n + q * 2;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + size + " " + size + '" width="' + (px || 192) + '" height="' + (px || 192) +
      '" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><path d="' + d + '" fill="#000"/></svg>';
  }
  root.QR = { svg: svg, encode: encode };
  if (typeof module !== "undefined") module.exports = root.QR;
})(typeof window !== "undefined" ? window : this);
