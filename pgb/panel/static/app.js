"use strict";
(function () {
  var BASE = location.pathname.replace(/\/?$/, "/");
  var $app = document.getElementById("app");
  var state = { me: null, timers: [], es: null };

  /* ---------- helpers ---------- */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;            // only used with our own static strings
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { add(el, x); });
    else el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function api(path, opt) {
    opt = opt || {};
    var init = { method: opt.method || (opt.body ? "POST" : "GET"), headers: { "X-PGB": "1" }, credentials: "same-origin" };
    if (opt.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opt.body); }
    if (opt.raw) { init.body = opt.raw; }
    return fetch(BASE + "api" + path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401 && path !== "/login") { state.me = null; route(); }
        if (!r.ok) { var e = new Error(j.error || ("HTTP " + r.status)); e.data = j; e.status = r.status; throw e; }
        return j;
      });
    });
  }
  function toast(msg, bad) {
    var t = h("div", { class: "toast" + (bad ? " err" : ""), role: bad ? "alert" : "status" }, msg);
    document.getElementById("toasts").appendChild(t);
    setTimeout(function () { t.remove(); }, bad ? 7000 : 3500);
  }
  function fail(e) { toast(e.message || String(e), true); }
  var t = I18N.t;
  function nf(n) { return Number(n).toLocaleString(I18N.locale()); }
  function bytes(n) { if (n == null) return "—"; var u = ["B", "KB", "MB", "GB", "TB"], i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return n.toFixed(i ? 1 : 0) + " " + u[i]; }
  function ago(ts) {
    if (!ts) return "—";
    var s = Math.max(0, Date.now() / 1000 - ts), rf = new Intl.RelativeTimeFormat(I18N.locale(), { numeric: "auto" });
    if (s < 90) return rf.format(0, "second");
    if (s < 5400) return rf.format(-Math.round(s / 60), "minute");
    if (s < 129600) return rf.format(-Math.round(s / 3600), "hour");
    return rf.format(-Math.round(s / 86400), "day");
  }
  function dt(ts) { return ts ? new Date(ts * 1000).toLocaleString(I18N.locale()) : "—"; }
  var fa = nf;
  function uptime(sec) { return sec >= 86400 ? t("u.days", { n: nf(Math.floor(sec / 86400)) }) : sec >= 3600 ? t("u.hours", { n: nf(Math.floor(sec / 3600)) }) : t("u.minutes", { n: nf(Math.max(1, Math.floor(sec / 60))) }); }
  /* theme: follows the system until the user picks something */
  function themePref() { try { return localStorage.getItem("pgb_theme") || "system"; } catch (e) { return "system"; } }
  function applyTheme(v) {
    try { if (v === "system") localStorage.removeItem("pgb_theme"); else localStorage.setItem("pgb_theme", v); } catch (e) { }
    if (v === "light" || v === "dark") document.documentElement.setAttribute("data-theme", v); else document.documentElement.removeAttribute("data-theme");
  }
  applyTheme(themePref());
  function prefs() {
    var th = themePref();
    var themeSel = h("div", { class: "seg", role: "group", "aria-label": t("c.theme") }, ["system", "light", "dark"].map(function (v) {
      return h("button", { type: "button", class: v === th ? "on" : "", "aria-pressed": v === th ? "true" : "false", title: t("th." + v), onclick: function () { applyTheme(v); route(); } }, themeIcon(v));
    }));
    var langSel = h("div", { class: "seg", role: "group", "aria-label": t("c.language") }, I18N.langs.map(function (l) {
      return h("button", { type: "button", class: l === I18N.get() ? "on" : "", "aria-pressed": l === I18N.get() ? "true" : "false", lang: l, title: I18N.name(l), onclick: function () { I18N.set(l); route(); } }, l.toUpperCase());
    }));
    return h("div", { class: "prefs" }, langSel, themeSel);
  }
  function themeIcon(v) {
    var p = { system: "M4 5h16v11H4zM9 20h6M12 16v4", light: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.900m14.200 14.200-1.400-1.400M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", dark: "M20 14.500A8 8 0 0 1 9.500 4 8 8 0 1 0 20 14.500z" }[v];
    return h("span", { html: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="' + p + '"/></svg>' }).firstChild;
  }
  function clearTimers() { state.timers.forEach(clearInterval); state.timers = []; if (state.es) { state.es.close(); state.es = null; } }
  function every(ms, fn) { state.timers.push(setInterval(fn, ms)); }
  function modal(title, body, actions) {
    var m = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title,
      onclick: function (e) { if (e.target === m) close(); } },
      h("div", { class: "card" }, h("h2", null, title), body, h("div", { class: "row", style: "margin-top:14px;justify-content:flex-end" }, actions(close))));
    function onKey(e) { if (e.key === "Escape") close(); }
    function close() { m.remove(); document.removeEventListener("keydown", onKey); }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(m);
    var first = m.querySelector("input,select,button"); if (first) first.focus();
    return close;
  }
  function field(label, input) { return h("label", { class: "f" }, h("span", null, label), input); }
  function btn(text, fn, cls) { return h("button", { class: "btn " + (cls || ""), type: "button", onclick: fn }, text); }
  var ICON = {
    home: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    db: "M12 3c4.4 0 8 1.300 8 3s-3.600 3-8 3-8-1.300-8-3 3.600-3 8-3zm8 3v6c0 1.700-3.600 3-8 3s-8-1.300-8-3V6m16 6v6c0 1.700-3.600 3-8 3s-8-1.300-8-3v-6",
    clock: "M12 7v5l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
    server: "M4 5h16v6H4zm0 8h16v6H4zM8 8h.01M8 16h.01",
    swap: "M7 7h13l-4-4m4 14H7l4 4M4 7l0 0",
    list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    shield: "M12 3l8 3v6c0 5-3.500 8-8 9-4.500-1-8-4-8-9V6z"
  };
  function icon(name) { return h("span", { html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="' + ICON[name] + '"/></svg>' }).firstChild; }

  /* ---------- shell ---------- */
  var PAGES = [
    ["dashboard", "nav.dashboard", "home"], ["backups", "nav.backups", "db"], ["schedulers", "nav.schedulers", "clock"],
    ["servers", "nav.servers", "server"], ["convert", "nav.convert", "swap"], ["jobs", "nav.jobs", "list"], ["security", "nav.security", "shield"]
  ];
  function shell(active, content) {
    var side = h("aside", { class: "side", id: "side" },
      h("div", { class: "brand" }, h("img", { src: "static/favicon.svg", width: 30, height: 30, alt: "" }), "PG-Backup"),
      h("nav", { class: "nav" }, PAGES.map(function (p) {
        return h("a", { href: "#/" + p[0], class: p[0] === active ? "on" : "", onclick: function () { side.classList.remove("open"); } }, icon(p[2]), t(p[1]));
      })),
      h("div", { class: "sp" }),
      prefs(),
      h("div", { class: "sub ltr mono", style: "margin:0;padding:0 10px;font-size:11px" }, "v" + (state.me.version || "") + " · " + (state.me.build || "")),
      h("div", { class: "row", style: "padding:8px" }, h("span", { class: "pill" }, state.me.user),
        btn(t("c.logout"), function () { api("/logout", { method: "POST" }).then(function () { state.me = null; route(); }); }, "sm")));
    return h("div", { class: "shell" }, side, h("div", { class: "scrim", onclick: function () { side.classList.remove("open"); } }), h("div", null,
      h("div", { class: "top" }, h("strong", null, "PG-Backup"), btn("☰", function () { side.classList.toggle("open"); }, "sm")),
      h("main", { class: "main" }, content)));
  }
  function page(title, sub, children) { return [h("h1", null, title), h("p", { class: "sub" }, sub || ""), children]; }

  /* ---------- login ---------- */
  function loginView() {
    var need = false, u = h("input", { type: "text", autocomplete: "username", required: true, autofocus: true }),
      p = h("input", { type: "password", autocomplete: "current-password", required: true }),
      o = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", class: "ltr", placeholder: t("login.otpPh") }),
      otpBox = h("div", { style: "display:none" }, field(t("login.otp"), o)), err = h("div", { class: "note warn", style: "display:none", role: "alert" });
    var f = h("form", { onsubmit: function (e) {
      e.preventDefault(); err.style.display = "none";
      api("/login", { body: { username: u.value, password: p.value, otp: o.value } }).then(function (r) {
        if (r.need_otp) { need = true; otpBox.style.display = ""; o.focus(); return; }
        return api("/me").then(function (m) { state.me = m; location.hash = "#/dashboard"; route(); });
      }).catch(function (x) {
        err.textContent = x.status === 429 ? t("login.tooMany", { s: x.data.retry_after || "" }) : x.message;
        err.style.display = "";
      });
    } }, field(t("login.user"), u), field(t("login.pass"), p), otpBox, err, h("button", { class: "btn pri", style: "width:100%;justify-content:center;margin-top:8px" }, t("login.submit")));
    return h("div", { class: "login" }, h("div", { class: "card" }, h("div", { class: "brand" }, h("img", { src: "static/favicon.svg", width: 34, height: 34, alt: "" }), "PG-Backup"),
      h("p", { class: "sub", style: "text-align:center" }, t("login.sub")), f, h("div", { style: "margin-top:18px;display:flex;justify-content:center" }, prefs())));
  }

  /* ---------- dashboard ---------- */
  function meter(pct) { return h("div", { class: "meter" + (pct > 85 ? " hot" : "") }, h("i", { style: "width:" + Math.min(100, Math.max(0, pct)) + "%" })); }
  function dashboard() {
    var box = h("div"); function load() {
      api("/dashboard").then(function (d) {
        var s = d.system, memUsed = s.mem_total ? (1 - s.mem_avail / s.mem_total) * 100 : 0, diskUsed = s.disk_total ? (1 - s.disk_free / s.disk_total) * 100 : 0;
        clear(box);
        box.appendChild(h("div", { class: "grid g4" },
          h("div", { class: "card stat" }, h("div", { class: "k" }, t("dash.load")), h("div", { class: "v ltr", style: "font-size:21px;white-space:nowrap" }, (s.load || []).join("  ") || "—"), h("div", { class: "s" }, t("dash.uptime", { v: s.uptime ? uptime(s.uptime) : "—" }))),
          h("div", { class: "card stat" }, h("div", { class: "k" }, t("dash.mem")), h("div", { class: "v" }, nf(Math.round(memUsed)) + "%"), h("div", { class: "s ltr" }, bytes(s.mem_total - s.mem_avail) + " / " + bytes(s.mem_total)), meter(memUsed)),
          h("div", { class: "card stat" }, h("div", { class: "k" }, t("dash.disk")), h("div", { class: "v" }, nf(Math.round(diskUsed)) + "%"), h("div", { class: "s" }, t("dash.free", { v: bytes(s.disk_free) })), meter(diskUsed)),
          h("div", { class: "card stat" }, h("div", { class: "k" }, t("dash.backups")), h("div", { class: "v" }, nf(d.backups.count)), h("div", { class: "s" }, bytes(d.backups.size) + (d.backups.latest ? " · " + t("dash.latest", { v: ago(d.backups.latest.mtime) }) : "")))));
        var sch = d.schedulers.length ? h("div", null, d.schedulers.map(function (x) {
          return h("div", { class: "row sb", style: "padding:6px 0" }, h("span", null, x.name, " ", h("span", { class: "pill" }, t("dash.everyH", { n: nf(x.interval) }))), h("span", { class: "pill " + (x.state === "active" ? "ok" : "bad") }, x.state === "active" ? t("st.active") : x.state === "none" ? t("st.none") : t("st.stopped")));
        })) : h("div", { class: "empty" }, t("dash.noSched"));
        var jobs = d.jobs.length ? jobTable(d.jobs) : h("div", { class: "empty" }, t("dash.noJobs"));
        box.appendChild(h("div", { class: "grid g2", style: "margin-top:14px" }, h("div", { class: "card" }, h("h2", null, t("dash.sched")), sch), h("div", { class: "card" }, h("h2", null, t("dash.jobs")), jobs)));
      }).catch(fail);
    }
    load(); every(8000, load);
    return page(t("nav.dashboard"), t("dash.sub"), box);
  }
  function statusPill(s) { return h("span", { class: "pill " + (s === "done" ? "ok" : s === "failed" ? "bad" : "warn") }, { done: t("st.done"), failed: t("st.failed"), running: t("st.running") }[s] || s); }
  function jobTable(items) {
    return h("div", { class: "tbl" }, h("table", null, h("tbody", null, items.map(function (j) {
      return h("tr", null, h("td", null, h("a", { href: "#/jobs/" + j.id }, "#" + j.id + " " + j.title)), h("td", null, statusPill(j.status)), h("td", null, ago(j.started)));
    }))));
  }

  /* ---------- job viewer ---------- */
  function openJob(jid, onDone) {
    var log = h("pre", { class: "log", "aria-live": "off" }), st = h("span", { class: "pill warn" }, t("st.running"));
    var close = modal(t("jb.job", { n: jid }), h("div", null, h("div", { style: "margin-bottom:8px" }, st), log), function (c) {
      return [btn(t("c.close"), function () { if (es) es.close(); c(); if (onDone) onDone(); }), btn(t("jb.cancel"), function () { api("/jobs/" + jid + "/cancel", { method: "POST" }).catch(fail); }, "bad")];
    });
    var es = new EventSource(BASE + "api/jobs/" + jid + "/stream");
    es.onmessage = function (e) { log.appendChild(document.createTextNode(JSON.parse(e.data) + "\n")); log.scrollTop = log.scrollHeight; };
    es.addEventListener("end", function (e) { es.close(); clear(st); st.className = "pill " + (e.data === "done" ? "ok" : "bad"); st.textContent = e.data === "done" ? t("st.done") : t("st.failed"); if (onDone) onDone(); });
    es.onerror = function () { es.close(); };
  }

  /* ---------- backups ---------- */
  function backups() {
    var box = h("div");
    function load() {
      api("/backups").then(function (d) {
        clear(box);
        var actions = h("div", { class: "row", style: "margin-bottom:14px" },
          btn("＋ " + t("bk.new"), function () {
            var node = h("input", { type: "checkbox" });
            modal(t("bk.new"), h("label", { class: "chk" }, node, t("bk.node")), function (c) {
              return [btn(t("c.cancel"), c), btn(t("c.start"), function () { c(); api("/backups/create", { body: { include_node: node.checked } }).then(function (r) { openJob(r.job, load); }).catch(fail); }, "pri")];
            });
          }, "pri"),
          h("label", { class: "btn" }, "⬆ " + t("bk.upload"), h("input", { type: "file", accept: ".zip", style: "display:none", onchange: function (e) {
            var f = e.target.files[0]; if (!f) return;
            fetch(BASE + "api/backups/upload?name=" + encodeURIComponent(f.name), { method: "POST", headers: { "X-PGB": "1" }, body: f, credentials: "same-origin" })
              .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error); toast(t("bk.uploaded")); load(); }); }).catch(fail);
          } })));
        var rows = d.items.map(function (b) {
          return h("tr", null, h("td", { class: "ltr mono" }, b.name), h("td", { class: "num" }, bytes(b.size)), h("td", null, dt(b.mtime)),
            h("td", null, h("div", { class: "row" },
              h("a", { class: "btn sm", href: BASE + "api/backups/" + encodeURIComponent(b.name) + "/download" }, t("bk.download")),
              btn(t("bk.restore"), function () { restore(b.name); }, "sm"),
              btn(t("bk.telegram"), function () { api("/backups/" + b.name + "/telegram", { body: {} }).then(function (r) { openJob(r.job); }).catch(fail); }, "sm"),
              btn(t("c.delete"), function () { modal(t("bk.delTitle"), h("p", null, t("bk.delBody", { n: b.name })), function (c) { return [btn(t("c.cancel"), c), btn(t("c.delete"), function () { c(); api("/backups/" + b.name, { method: "DELETE" }).then(load).catch(fail); }, "bad")]; }); }, "sm bad"))));
        });
        box.appendChild(actions);
        box.appendChild(h("div", { class: "card tbl" }, rows.length ? h("table", null, h("thead", null, h("tr", null, h("th", null, t("bk.file")), h("th", null, t("bk.size")), h("th", null, t("bk.date")), h("th", null, ""))), h("tbody", null, rows)) : h("div", { class: "empty" }, t("bk.none")),
          h("div", { class: "sub ltr", style: "margin:10px 0 0" }, d.dir)));
      }).catch(fail);
    }
    function restore(name) {
      var c = h("input", { type: "text", class: "ltr", placeholder: name });
      modal(t("bk.restoreTitle"), h("div", null, h("div", { class: "note warn" }, t("bk.restoreWarn")),
        h("p", null, t("bk.restoreType")), c), function (close) {
        return [btn(t("c.cancel"), close), btn(t("bk.restore"), function () { close(); api("/backups/" + name + "/restore", { body: { confirm: c.value.trim() } }).then(function (r) { openJob(r.job, load); }).catch(fail); }, "bad")];
      });
    }
    load();
    return page(t("nav.backups"), t("bk.sub"), box);
  }

  /* ---------- schedulers ---------- */
  function schedulers() {
    var box = h("div");
    function load() {
      api("/schedulers").then(function (d) {
        clear(box);
        box.appendChild(h("div", { class: "row", style: "margin-bottom:14px" }, btn("＋ " + t("sc.new"), createSched, "pri")));
        if (!d.items.length) { box.appendChild(h("div", { class: "card empty" }, t("sc.none"))); return; }
        d.items.forEach(function (s) { box.appendChild(card(s)); });
      }).catch(fail);
    }
    function createSched() {
      var name = h("input", { type: "text", class: "ltr", placeholder: "backup-1", maxlength: 64 }),
        tok = h("input", { type: "password", class: "ltr", placeholder: "123456789:AA…", autocomplete: "off" }),
        chat = h("input", { type: "text", class: "ltr", placeholder: "123456789 / -100… / @channel" }),
        proxy = h("input", { type: "text", class: "ltr", placeholder: t("sc.proxyPh") }),
        iv = h("input", { type: "number", step: "0.25", min: "0.05", value: 1 }), node = h("input", { type: "checkbox" }), skip = h("input", { type: "checkbox" });
      modal(t("sc.new"), h("div", null, h("p", { class: "note" }, t("sc.newHint")),
        h("div", { class: "grid g2" }, field(t("sc.token"), tok), field(t("sc.chat"), chat), field(t("sc.interval"), iv), field(t("sc.name"), name)),
        field(t("sc.proxy"), proxy), h("label", { class: "chk" }, node, t("sc.node")), h("label", { class: "chk" }, skip, t("sc.skipTest"))), function (c) {
        return [btn(t("c.cancel"), c), btn(t("sc.create"), function () {
          api("/schedulers/create", { body: { name: name.value, token: tok.value, chat: chat.value, proxy: proxy.value, interval: iv.value, node: node.checked, test: !skip.checked } })
            .then(function () { c(); toast(t("sc.created")); load(); }).catch(fail);
        }, "pri")];
      });
    }
    function card(s) {
      var tok = h("input", { type: "password", class: "ltr", placeholder: t("sc.tokenKeep", { hint: s.token_hint || "—" }), autocomplete: "off" }),
        chat = h("input", { type: "text", class: "ltr", value: s.chat || "" }), proxy = h("input", { type: "text", class: "ltr", placeholder: t("sc.proxyPh") }),
        iv = h("input", { type: "number", step: "0.25", min: "0.05", value: s.interval }), node = h("input", { type: "checkbox" });
      node.checked = !!s.node;
      function act(a) { return function () { api("/schedulers/" + s.name + "/" + a, { method: "POST" }).then(function () { toast(t("c.done")); load(); }).catch(fail); }; }
      function save(restart) {
        var body = { chat: chat.value, interval: iv.value, node: node.checked, restart: restart };
        if (tok.value) body.token = tok.value; if (proxy.value !== "") body.proxy = proxy.value;
        api("/schedulers/" + s.name + "/update", { body: body }).then(function () { toast(t("c.saved")); tok.value = ""; load(); }).catch(fail);
      }
      var logBox = null, logTimer = null;
      function toggleLogs(e) {
        if (logBox) { logBox.remove(); logBox = null; clearInterval(logTimer); return; }
        logBox = h("pre", { class: "log" }); e.target.parentNode.parentNode.appendChild(logBox);
        function pull() { api("/schedulers/" + s.name + "/logs?n=200").then(function (r) { if (!logBox) return; logBox.textContent = r.log || t("c.emptyLog"); logBox.scrollTop = logBox.scrollHeight; }).catch(function () { }); }
        pull(); logTimer = setInterval(pull, 3000); state.timers.push(logTimer);
      }
      var isSvc = s.kind === "systemd", managed = s.managed;
      var kindPill = h("span", { class: "pill" }, isSvc ? "systemd" : s.kind === "none" ? t("sc.notInstalled") : s.kind);
      var controls = h("div", { class: "row" });
      if (isSvc) { controls.appendChild(btn(t("c.start"), act("start"), "sm")); controls.appendChild(btn(t("c.stop"), act("stop"), "sm")); controls.appendChild(btn(t("c.restart"), act("restart"), "sm")); controls.appendChild(btn(t("sc.logs"), toggleLogs, "sm")); }
      else if (s.kind === "screen" || s.kind === "tmux") controls.appendChild(btn(t("c.stop"), act("stop"), "sm"));
      controls.appendChild(btn(t("c.delete"), function () { if (window.confirm(t("sc.removeQ", { name: s.name }))) api("/schedulers/" + s.name, { method: "DELETE" }).then(function () { toast(t("c.done")); load(); }).catch(fail); }, "sm bad"));
      var el = h("div", { class: "card", style: "margin-bottom:14px" },
        h("div", { class: "row sb", style: "margin-bottom:12px" }, h("h2", { style: "margin:0" }, s.name, " ", h("span", { class: "pill " + (s.state === "active" ? "ok" : "bad") }, s.state === "active" ? t("st.active") : t("st.stopped")), " ", kindPill), controls),
        managed && isSvc ? null : h("div", { class: "note", style: "margin-bottom:10px" }, t(s.legacy ? "sc.legacyNote" : "sc.oldNote"), " ",
          btn(t("sc.convert"), function () { api("/schedulers/" + s.name + "/upgrade", { method: "POST" }).then(function () { toast(t("c.done")); load(); }).catch(fail); }, "sm pri")),
        managed ? h("div", null,
          h("div", { class: "grid g2" }, field(t("sc.token"), tok), field(t("sc.chat"), chat), field(t("sc.proxy"), proxy), field(t("sc.interval"), iv)),
          h("label", { class: "chk" }, node, t("sc.node")),
          h("div", { class: "row", style: "margin-top:8px" }, btn(t("c.save"), function () { save(false); }), btn(t("sc.saveRestart"), function () { save(true); }, "pri"),
            btn(t("sc.test"), function () {
              api("/telegram/test", { body: { instance: s.name, token: tok.value, chat: chat.value, proxy: proxy.value } }).then(function (r) { toast(r.message, !r.ok); }).catch(fail);
            }))) : null);
      return el;
    }
    load(); every(10000, function () { if (!document.querySelector(".modal") && !document.querySelector("pre.log")) load(); });
    return page(t("nav.schedulers"), t("sc.sub"), box);
  }

  /* ---------- servers / transfer ---------- */
  function servers() {
    var box = h("div");
    function load() {
      api("/servers").then(function (d) {
        clear(box);
        box.appendChild(h("div", { class: "row", style: "margin-bottom:14px" }, btn("＋ " + t("sv.add"), addServer, "pri")));
        box.appendChild(h("div", { class: "card tbl" }, d.items.length ? h("table", null, h("thead", null, h("tr", null, h("th", null, t("sv.name")), h("th", null, t("sv.address")), h("th", null, t("sv.login")), h("th", null, ""))),
          h("tbody", null, d.items.map(function (s) {
            return h("tr", null, h("td", null, s.name), h("td", { class: "ltr mono" }, s.user + "@" + s.host + ":" + s.port), h("td", null, s.auth === "key" ? t("sv.authKey") : t("sv.pass")),
              h("td", null, h("div", { class: "row" }, btn(t("sv.transfer"), function () { transfer(s); }, "sm pri"),
                btn(t("c.delete"), function () { api("/servers/" + s.id, { method: "DELETE" }).then(load).catch(fail); }, "sm bad"))));
          }))) : h("div", { class: "empty" }, t("sv.none"))));
      }).catch(fail);
    }
    function addServer() {
      var n = h("input", { type: "text" }), host = h("input", { type: "text", class: "ltr", required: true }), port = h("input", { type: "number", value: 22 }), user = h("input", { type: "text", class: "ltr", value: "root" }),
        pw = h("input", { type: "password", autocomplete: "off" }), key = h("textarea", { rows: 4, class: "ltr mono", placeholder: "-----BEGIN OPENSSH PRIVATE KEY-----" }),
        fp = h("input", { type: "text", class: "ltr mono", placeholder: "SHA256:..." });
      modal(t("sv.add"), h("div", null, field(t("sv.name"), n), field(t("sv.address"), host), h("div", { class: "grid g2" }, field(t("sv.sshPort"), port), field(t("sv.user"), user)), field(t("sv.pass"), pw), field(t("sv.key"), key), field(t("sv.hostfp"), fp),
        h("div", { class: "note" }, t("sv.note"))), function (c) {
        return [btn(t("c.cancel"), c), btn(t("c.save"), function () {
          api("/servers/add", { body: { name: n.value, host: host.value, port: port.value, user: user.value, password: pw.value, private_key: key.value, host_fp: fp.value } }).then(function () { c(); load(); }).catch(fail);
        }, "pri")];
      });
    }
    function transfer(s) {
      var node = h("input", { type: "checkbox" }), c = h("input", { type: "text", class: "ltr", placeholder: s.host });
      modal(t("sv.transferTitle", { n: s.name }), h("div", null, h("div", { class: "note warn" }, t("sv.transferWarn")),
        h("label", { class: "chk" }, node, t("sc.node")), h("p", null, t("sv.confirmHost")), c), function (close) {
        return [btn(t("c.cancel"), close), btn(t("sv.go"), function () { close(); api("/transfer", { body: { server: s.id, include_node: node.checked, confirm: c.value.trim() } }).then(function (r) { openJob(r.job); }).catch(fail); }, "bad")];
      });
    }
    load();
    return page(t("nav.servers"), t("sv.sub"), box);
  }

  /* ---------- database engine conversion ---------- */
  var ENGINES = { postgresql: "PostgreSQL", timescaledb: "TimescaleDB", mysql: "MySQL", mariadb: "MariaDB", sqlite: "SQLite" };
  function convertPage() {
    var box = h("div");
    function load() {
      api("/backups").then(function (d) {
        clear(box);
        var to = h("select", { class: "ltr" }, ["postgresql", "timescaledb", "mysql", "mariadb"].map(function (k) { return h("option", { value: k }, ENGINES[k]); }));
        box.appendChild(h("div", { class: "card" },
          h("div", { class: "note", style: "margin-bottom:14px" }, t("cv.safe")),
          h("h2", null, t("cv.live")), h("p", { class: "sub" }, t("cv.liveHint")),
          h("div", { class: "row" }, field(t("cv.target"), to),
            btn(t("cv.liveBtn"), function () { api("/convert", { body: { source: "live", to: to.value } }).then(function (r) { openJob(r.job, load); }).catch(fail); }, "pri"))));
        var rows = d.items.map(function (b) {
          var cell = h("td", null, "…"), act = h("td", null);
          api("/convert/" + encodeURIComponent(b.name)).then(function (i) {
            cell.textContent = ENGINES[i.type] || "?";
            (i.targets || []).forEach(function (k) {
              act.appendChild(btn("→ " + ENGINES[k], function () { api("/convert", { body: { source: b.name, to: k } }).then(function (r) { openJob(r.job, load); }).catch(fail); }, "sm pri"));
            });
            if (!(i.targets || []).length) act.appendChild(h("span", { class: "sub" }, t("cv.none")));
          }).catch(function () { cell.textContent = "?"; });
          return h("tr", null, h("td", { class: "ltr mono" }, b.name), cell, h("td", null, h("div", { class: "row" }, act,
            h("a", { class: "btn sm", href: BASE + "api/backups/" + encodeURIComponent(b.name) + "/download" }, t("bk.download")))));
        });
        box.appendChild(h("div", { class: "card tbl", style: "margin-top:14px" }, h("h2", null, t("cv.files")),
          rows.length ? h("table", null, h("thead", null, h("tr", null, h("th", null, t("bk.file")), h("th", null, t("cv.engine")), h("th", null, ""))), h("tbody", null, rows)) : h("div", { class: "empty" }, t("bk.none")),
          h("p", { class: "sub" }, t("cv.pairs"))));
      }).catch(fail);
    }
    load();
    return page(t("cv.title"), t("cv.sub"), box);
  }

  /* ---------- jobs ---------- */
  function jobs(id) {
    var box = h("div");
    function load() { api("/jobs").then(function (d) { clear(box); box.appendChild(h("div", { class: "card" }, d.items.length ? jobTable(d.items) : h("div", { class: "empty" }, t("jb.none")))); }).catch(fail); }
    load(); every(5000, load);
    if (id) setTimeout(function () { openJob(+id, load); }, 0);
    return page(t("nav.jobs"), t("jb.sub"), box);
  }

  /* ---------- security ---------- */
  function security() {
    var box = h("div");
    function load() {
      api("/me").then(function (me) {
        state.me = me; clear(box);
        var old = h("input", { type: "password", autocomplete: "current-password" }), nw = h("input", { type: "password", autocomplete: "new-password" });
        var pwCard = h("div", { class: "card" }, h("h2", null, t("se.pw")), field(t("se.pwOld"), old), field(t("se.pwNew"), nw),
          btn(t("se.pwBtn"), function () { api("/password", { body: { old: old.value, new: nw.value } }).then(function () { toast(t("se.pwDone")); old.value = nw.value = ""; }).catch(fail); }, "pri"));
        var tf = h("div", { class: "card" }, h("h2", null, t("se.tfa")));
        if (me.totp) {
          var pw = h("input", { type: "password" }), code = h("input", { type: "text", class: "ltr", inputmode: "numeric" });
          tf.appendChild(h("p", null, h("span", { class: "pill ok" }, t("se.on")), " — " + t("se.recLeft", { n: nf(me.recovery_left) })));
          tf.appendChild(field(t("login.pass"), pw)); tf.appendChild(field(t("se.curCode"), code));
          tf.appendChild(btn(t("se.disable"), function () { api("/2fa/disable", { body: { password: pw.value, code: code.value } }).then(function () { toast(t("se.disabled")); load(); }).catch(fail); }, "bad"));
        } else {
          tf.appendChild(h("p", { class: "sub", style: "margin-bottom:12px" }, t("se.tfaDesc")));
          tf.appendChild(btn(t("se.enable"), function () {
            api("/2fa/setup", { method: "POST" }).then(function (s) {
              var qr = h("div", { class: "qr", html: window.QR.svg(s.uri, 190) }), c = h("input", { type: "text", class: "ltr", inputmode: "numeric", placeholder: "123456" });
              clear(tf); tf.appendChild(h("h2", null, t("se.tfa")));
              tf.appendChild(h("div", { class: "row", style: "align-items:flex-start" }, qr, h("div", { style: "flex:1;min-width:220px" }, h("p", null, t("se.scan")), h("code", { class: "ltr mono", style: "word-break:break-all;display:block" }, s.secret),
                field(t("se.code6"), c), btn(t("se.verify"), function () {
                  api("/2fa/enable", { body: { code: c.value } }).then(function (r) {
                    modal(t("se.recTitle"), h("div", null, h("div", { class: "note warn" }, t("se.recWarn")),
                      h("pre", { class: "log" }, r.recovery_codes.join("\n"))), function (cl) { return [btn(t("se.recOk"), function () { cl(); load(); }, "pri")]; });
                  }).catch(fail);
                }, "pri"))));
            }).catch(fail);
          }, "pri"));
        }
        box.appendChild(h("div", { class: "grid g2" }, pwCard, tf));
        api("/audit").then(function (a) {
          box.appendChild(h("div", { class: "card tbl", style: "margin-top:14px" }, h("h2", null, t("se.audit")), h("table", null, h("thead", null, h("tr", null, [t("se.time"), t("sv.user"), t("se.ip"), t("se.event"), t("se.details")].map(function (x) { return h("th", null, x); }))),
            h("tbody", null, a.items.map(function (r) { return h("tr", null, h("td", null, dt(r.ts)), h("td", null, r.user), h("td", { class: "ltr mono" }, r.ip), h("td", null, r.event), h("td", { class: "ltr" }, r.detail || "")); })))));
        });
      }).catch(fail);
    }
    load();
    return page(t("nav.security"), t("se.sub"), box);
  }

  /* ---------- router ---------- */
  function route() {
    clearTimers();
    clear($app);
    if (!state.me) { $app.appendChild(loginView()); return; }
    var parts = (location.hash || "#/dashboard").slice(2).split("/"), name = parts[0] || "dashboard";
    var views = { dashboard: dashboard, backups: backups, schedulers: schedulers, servers: servers, convert: convertPage, jobs: function () { return jobs(parts[1]); }, security: security };
    if (!views[name]) name = "dashboard";
    $app.appendChild(shell(name, views[name]()));
  }
  /* label table cells so rows can be shown as stacked cards on phones */
  function labelTables() {
    var tabs = $app.querySelectorAll("table:not([data-lab])");
    Array.prototype.forEach.call(tabs, function (tb) {
      var heads = Array.prototype.map.call(tb.querySelectorAll("thead th"), function (x) { return x.textContent.trim(); });
      Array.prototype.forEach.call(tb.querySelectorAll("tbody tr"), function (tr) {
        Array.prototype.forEach.call(tr.children, function (td, i) { td.setAttribute("data-l", heads[i] || ""); });
      });
      tb.setAttribute("data-lab", "1");
    });
  }
  if (window.MutationObserver) new MutationObserver(labelTables).observe($app, { childList: true, subtree: true });
  window.addEventListener("hashchange", route);
  api("/me").then(function (m) { state.me = m; route(); }).catch(function () { state.me = null; route(); });
})();
