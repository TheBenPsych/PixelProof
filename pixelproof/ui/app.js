/* PixelProof desktop app. Talks only to the PixelProof engine on this PC (127.0.0.1). */
(function () {
"use strict";
var TOKEN = window.PP_TOKEN;
var S = {view: "home", cur: null, mode: "side", ph: null, period: "all", site: null, data: null, onlyNew: null};
var CAT = {exact: "Exact copy", edited: "Edited copy", possible: "Possible"};
var RANK = {exact: 0, edited: 1, possible: 2};
var PALETTE = ["#0C9A9A", "#5156CF", "#D63A6E", "#A86300", "#1C8A57", "#7A4FD0", "#C2410C", "#0E7490", "#BE185D", "#4D7C0F"];

/* ---------- talking to the engine ---------- */
function api(path, body) {
  var opts = {method: body === undefined ? "GET" : "POST", headers: {"X-PixelProof": TOKEN}};
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  return fetch(path, opts).then(function (r) { return r.json(); }).catch(function () {
    return {ok: false, error: "PixelProof's engine is not running. Start it again with PixelProof.bat."};
  });
}
function act(path, body, okText) {
  return api(path, body).then(function (r) {
    if (r && r.ok === false) { toast(r.error || "That did not work"); return r; }
    if (okText) toast(okText);
    return refresh().then(function () { return r; });
  });
}
function refresh() {
  return api("/api/state").then(function (d) { if (d && d.findings) { S.data = d; render(); } });
}
function pic(kind, id, size) { return "/img/" + kind + "/" + id + "?s=" + (size || 360) + "&t=" + encodeURIComponent(TOKEN); }

/* ---------- helpers ---------- */
function el(tag, attrs, kids) {
  var e = document.createElement(tag);
  if (attrs) for (var k in attrs) {
    var v = attrs[k]; if (v == null || v === false) continue;
    if (k === "text") e.textContent = v; else if (k === "html") e.innerHTML = v;
    else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  (kids || []).forEach(function (c) { if (c != null && c !== false) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
  return e;
}
function F() { return S.data ? S.data.findings : []; }
function byId(id) { return F().filter(function (m) { return m.id === id; })[0]; }
function shown(m) { return !m.banned; }
function isOpen(m) { return shown(m) && !m.licensed && m.status === "new"; }
function queue() {
  return F().filter(isOpen).sort(function (a, b) { return RANK[a.category] - RANK[b.category] || a.id - b.id; });
}
function plural(n, w, ws) { return n + " " + (n === 1 ? w : (ws || w + "s")); }
function hashCode(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return Math.abs(h); }
function colorOf(name) { return PALETTE[hashCode(name || "?") % PALETTE.length]; }
function initials(n) { return (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(function (p) { return p[0].toUpperCase(); }).join(""); }
function avatar(name, sm) { return el("div", {"class": "avatar" + (sm ? " sm" : ""), style: "background:" + colorOf(name), text: initials(name), title: name}); }
function when(iso, withTime) {
  if (!iso) return "–";
  var d = new Date(iso); if (isNaN(d)) return iso;
  var s = d.toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
  return withTime ? s + ", " + d.toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"}) : s;
}
function pill(cat) { return el("span", {"class": "pill p-" + cat, text: CAT[cat] || cat}); }
function stPill(m) {
  if (m.licensed) return el("span", {"class": "pill p-ok", text: "Licensed"});
  if (m.status === "stolen") return el("span", {"class": "pill p-stolen", text: "Stolen"});
  if (m.status === "not_match") return el("span", {"class": "pill p-no", text: "Not a match"});
  return null;
}
function pageOf(m) { return m.page_url || m.image_url; }
var toastT;
function toast(t) { var e = document.getElementById("toast"); e.textContent = t; e.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function () { e.classList.remove("show"); }, 3200); }
function go(view, extra) { S.view = view; if (extra) for (var k in extra) S[k] = extra[k]; render(); window.scrollTo(0, 0); }
function openUrl(u) { api("/api/open-url", {url: u}).then(function (r) { if (r.ok === false) toast(r.error); }); }
function siblings(m) {
  /* the same photo of ours, on the same article, on its sister sites */
  if (!m.path_key || !m.company) return [];
  return F().filter(function (x) { return x.id !== m.id && x.image_id === m.image_id && x.path_key === m.path_key && x.company === m.company; });
}
function repeats(m) { return F().filter(function (x) { return x.id !== m.id && x.image_id === m.image_id; }); }
function photographers() {
  var seen = {}, out = [];
  (S.data.library.photographers || []).forEach(function (p) { seen[p.name] = p; out.push(p); });
  F().forEach(function (m) { if (!seen[m.photographer]) { seen[m.photographer] = {name: m.photographer, images: 0, periods: []}; out.push(seen[m.photographer]); } });
  return out;
}
function approvedFor(name) {
  var r = (S.data.approved || []).filter(function (a) { return a.photographer.toLowerCase() === (name || "").toLowerCase(); })[0];
  return r ? r.sites : [];
}
function svg(d) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + d + "</svg>"; }
var ICONS = {
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  review: '<rect x="3" y="5" width="8" height="14" rx="2"/><rect x="13" y="5" width="8" height="14" rx="2"/>',
  ph: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  sites: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c3 3 3 15 0 18c-3-3-3-15 0-18"/>',
  lists: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  lib: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>'
};

/* ---------- nav ---------- */
function renderNav() {
  var nav = document.getElementById("nav"); nav.innerHTML = "";
  [["home", "Home"], ["review", "Review"], ["ph", "Photographers"], ["sites", "Sites"], ["lists", "Site lists"], ["lib", "My images"]].forEach(function (n) {
    var b = el("button", {type: "button", "aria-current": S.view === n[0] ? "page" : null, onclick: function () { go(n[0], n[0] === "review" ? {cur: null} : null); }});
    b.innerHTML = svg(ICONS[n[0]]); b.appendChild(el("span", {text: n[1]}));
    if (n[0] === "review") { var q = queue().length; if (q) b.appendChild(el("span", {"class": "count", text: String(q)})); }
    nav.appendChild(b);
  });
  var lib = S.data.library, foot = document.getElementById("foot"); foot.innerHTML = "";
  foot.appendChild(el("span", {}, [el("span", {"class": "dot"}), lib.images ? "Library ready" : "Library empty"]));
  foot.appendChild(el("span", {text: lib.images.toLocaleString() + " images · " + plural(lib.photographers.length, "photographer")}));
  if (S.data.device) foot.appendChild(el("span", {text: S.data.device.replace(/^graphics card \((.*)\)$/, "$1")}));
  foot.appendChild(el("span", {text: "PixelProof " + S.data.version}));
  foot.appendChild(el("button", {type: "button", text: "Open my proofs folder", onclick: function () { api("/api/open-path", {what: "proofs"}); }}));
}

/* ---------- pair card ---------- */
function pairCard(m) {
  return el("button", {"class": "pair" + (m.status === "not_match" ? " dim" : ""), type: "button", onclick: function () { go("review", {cur: m.id}); }}, [
    el("div", {"class": "two"}, [
      el("img", {"class": "o", src: pic("ours", m.image_id), alt: "Ours", loading: "lazy"}),
      el("img", {src: pic("theirs", m.id), alt: "Theirs on " + m.domain, loading: "lazy"})
    ]),
    el("div", {"class": "t"}, [
      el("div", {}, [el("b", {text: m.domain || "File on this PC"}), el("span", {"class": "d", text: m.photographer + " · " + when(m.found_at)})]),
      stPill(m) || pill(m.category)
    ])
  ]);
}
function grid(list, emptyText) {
  var g = el("div", {"class": "grid"});
  list.forEach(function (m) { g.appendChild(pairCard(m)); });
  if (!list.length) return el("div", {"class": "empty", text: emptyText || "Nothing here yet."});
  return g;
}

/* ---------- HOME ---------- */
function greeting() { var h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; }
function checkBox() {
  var input = el("input", {type: "text", id: "check-url", placeholder: "Paste a page link, like https://somesite.com/article/…", "aria-label": "Page link to check"});
  function go() { var v = input.value.trim(); if (!v) { toast("Paste a page link first"); input.focus(); return; } startCheck([v]); }
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
  return el("div", {"class": "l"}, [
    el("h2", {text: "Check one page"}),
    el("p", {text: "Seen something suspicious? Paste the link. PixelProof looks at every picture on that page and tells you if any are yours."}),
    el("div", {"class": "urlrow"}, [input, el("button", {"class": "btn ink", type: "button", text: "Check page", onclick: go})]),
    el("div", {"class": "minor"}, [
      el("button", {"class": "link", type: "button", text: "Check a list of links (.txt)…", onclick: function () {
        api("/api/pick", {kind: "list"}).then(function (r) { if (r.ok === false) return toast(r.error); if (r.links && r.links.length) startCheck(r.links); });
      }}),
      el("button", {"class": "link", type: "button", text: "Check a picture file…", onclick: function () {
        api("/api/pick", {kind: "file"}).then(function (r) { if (r.ok === false) return toast(r.error); if (r.path) startCheck([r.path]); });
      }})
    ])
  ]);
}
function heroArt() {
  var r = el("div", {"class": "r"});
  var best = F().filter(function (m) { return shown(m) && m.category === "exact" && m.status !== "not_match"; }).slice(-1)[0] || F().filter(shown).slice(-1)[0];
  if (best) {
    r.appendChild(el("div", {"class": "hero"}, [
      el("img", {src: pic("ours", best.image_id, 900), alt: ""}), el("img", {"class": "b", src: pic("theirs", best.id, 900), alt: ""}),
      el("div", {"class": "split"}), el("span", {"class": "lbl o", text: "OURS"}), el("span", {"class": "lbl t", text: "THEIRS · " + best.domain})
    ]));
  } else {
    var a = el("div", {"class": "hero-art"});
    a.innerHTML = '<svg width="220" height="150" viewBox="0 0 220 150" aria-hidden="true"><rect x="10" y="15" width="95" height="120" rx="12" fill="var(--ours-soft)" stroke="var(--ours)" stroke-width="3"/><rect x="115" y="15" width="95" height="120" rx="12" fill="var(--theirs-soft)" stroke="var(--theirs)" stroke-width="3"/><path d="M25 110l22-30 16 18 12-14 20 26z" fill="var(--ours)"/><path d="M130 110l22-30 16 18 12-14 20 26z" fill="var(--theirs)"/><circle cx="80" cy="45" r="9" fill="var(--ours)"/><circle cx="185" cy="45" r="9" fill="var(--theirs)"/></svg>';
    r.appendChild(a);
  }
  return r;
}
function home(main) {
  var d = S.data, q = queue();
  var stolen = F().filter(function (m) { return m.status === "stolen" && shown(m); }).length;
  var sitesWith = {}; F().forEach(function (m) { if (shown(m) && m.page_url && !m.licensed) sitesWith[m.domain] = 1; });
  main.appendChild(el("div", {"class": "head"}, [
    el("div", {}, [el("h1", {text: greeting() + ", Ben"}),
      el("p", {text: !d.library.images ? "Let's get your images in first." : q.length ? plural(q.length, "match", "matches") + " waiting for you to look at." : "Nothing waiting. Check a page whenever you spot something."})]),
    q.length ? el("button", {"class": "btn ink big", type: "button", onclick: function () { go("review", {cur: null}); }}, ["Start reviewing ", el("kbd", {text: "→"})]) : null
  ]));
  if (!d.library.images) {
    main.appendChild(el("div", {"class": "start"}, [el("div", {"class": "bignum", text: "1"}), el("div", {}, [
      el("h2", {text: "Add a folder of your images"}),
      el("p", {text: "PixelProof makes a small fingerprint of each image, so it can recognise them anywhere. Your images never leave this PC."}),
      el("button", {"class": "btn ink", type: "button", text: "Add my images", onclick: function () { go("lib"); }})])]));
  }
  main.appendChild(el("div", {"class": "check"}, [checkBox(), heroArt()]));
  function tile(k, v, s, hot, fn) { return el(fn ? "button" : "div", {"class": "tile" + (hot ? " hot" : ""), type: fn ? "button" : null, onclick: fn}, [el("span", {"class": "k", text: k}), el("span", {"class": "v", text: v}), el("span", {"class": "s", text: s})]); }
  var exact = q.filter(function (m) { return m.category === "exact"; }).length;
  main.appendChild(el("div", {"class": "tiles"}, [
    tile("To review", String(q.length), plural(exact, "exact copy", "exact copies"), q.length > 0, function () { go("review", {cur: null}); }),
    tile("Confirmed stolen", String(stolen), "with Proof of Discovery", false, null),
    tile("Sites with your images", String(Object.keys(sitesWith).length), plural(d.sisters.length, "sister-site group"), false, function () { go("sites"); }),
    tile("Images protected", d.library.images.toLocaleString(), plural(d.library.photographers.length, "photographer"), false, function () { go("ph"); })
  ]));
  var latest = F().filter(shown).slice().sort(function (a, b) { return b.id - a.id; }).slice(0, 8);
  main.appendChild(el("div", {"class": "row-between"}, [el("h2", {"class": "section-title", text: "Latest finds"}), latest.length ? el("button", {"class": "btn", type: "button", text: "See all sites", onclick: function () { go("sites"); }}) : null]));
  main.appendChild(grid(latest, "No matches yet. Check a page above and anything of yours shows up here."));
}

/* ---------- REVIEW ---------- */
function review(main) {
  var q = queue();
  var m = S.cur ? byId(S.cur) : q[0];
  if (!m) {
    main.appendChild(el("div", {"class": "head"}, [el("div", {}, [el("h1", {text: "All caught up"}), el("p", {text: "Nothing left to review. New matches show up here after you check a page."})])]));
    var done = F().filter(function (x) { return shown(x) && x.status !== "new"; }).sort(function (a, b) { return b.id - a.id; });
    if (done.length) { main.appendChild(el("h2", {"class": "section-title", text: "Already reviewed"})); main.appendChild(grid(done)); }
    else main.appendChild(el("div", {"class": "empty", text: "Your review list is empty."}));
    return;
  }
  var pos = q.indexOf(m), reviewed = F().filter(function (x) { return shown(x) && !x.licensed && x.status !== "new"; }).length, total = reviewed + q.length;
  main.appendChild(el("div", {"class": "head"}, [
    el("div", {}, [el("h1", {text: pos > -1 ? "Is this ours?" : "Match " + m.match_id}),
      el("p", {text: pos > -1 ? "Look at both pictures, then decide." : m.licensed ? "This site is approved for " + m.photographer + ", so this use is licensed." : "Already reviewed. You can still change it."})]),
    el("div", {"class": "seg", role: "group", "aria-label": "Compare mode"}, [["side", "Side by side"], ["swipe", "Swipe"]].map(function (o) {
      return el("button", {type: "button", "aria-pressed": String(S.mode === o[0]), text: o[1], onclick: function () { S.mode = o[0]; render(); }});
    }))
  ]));
  main.appendChild(el("div", {"class": "progress"}, [el("div", {"class": "bar"}, [el("i", {style: "width:" + (total ? Math.round(reviewed / total * 100) : 0) + "%"})]), el("span", {text: reviewed + " of " + total + " reviewed"})]));
  var stage = el("div", {"class": "stage"});
  var oursSize = m.our_w ? m.our_w + " × " + m.our_h : "", theirsSize = m.width ? m.width + " × " + m.height : "";
  if (S.mode === "side") {
    stage.appendChild(el("div", {"class": "sbs"}, [
      el("div", {"class": "side o"}, [el("div", {"class": "tag"}, [el("span", {"class": "o", text: "OURS"}), el("span", {"class": "m", text: oursSize})]), el("div", {"class": "frame"}, [el("img", {src: pic("ours", m.image_id, 900), alt: "Our image"})])]),
      el("div", {"class": "side t"}, [el("div", {"class": "tag"}, [el("span", {"class": "t", text: "THEIRS"}), el("span", {"class": "m", text: theirsSize})]), el("div", {"class": "frame"}, [el("img", {src: pic("theirs", m.id, 900), alt: "Their image"})])])
    ]));
  } else {
    var b = el("img", {src: pic("theirs", m.id, 900), alt: "Their image", style: "clip-path:inset(0 0 0 50%)"});
    var line = el("div", {"class": "lineH", style: "left:50%"});
    var range = el("input", {type: "range", min: "0", max: "100", value: "50", "aria-label": "Drag to compare ours and theirs"});
    range.addEventListener("input", function () { b.style.clipPath = "inset(0 0 0 " + range.value + "%)"; line.style.left = range.value + "%"; });
    stage.appendChild(el("div", {"class": "swipe"}, [el("img", {src: pic("ours", m.image_id, 900), alt: "Our image"}), b, line, el("span", {"class": "lbl o", text: "OURS"}), el("span", {"class": "lbl t", text: "THEIRS"}), range]));
  }
  if (m.licensed) {
    stage.appendChild(el("div", {"class": "callout ok"}, [el("b", {text: "Licensed. "}), m.domain + " is approved for " + m.photographer + " (" + m.licensed + "). It is not counted as stolen."]));
  } else if (m.status === "new") {
    stage.appendChild(el("div", {"class": "decide"}, [
      el("button", {"class": "btn good big", type: "button", onclick: function () { openConfirm(m); }}, ["Yes, it's stolen ", el("kbd", {text: "Y"})]),
      el("button", {"class": "btn big", type: "button", onclick: function () { decideNo(m); }}, ["Not a match ", el("kbd", {text: "N"})]),
      el("button", {"class": "btn big skip", type: "button", onclick: function () { skip(m); }}, ["Skip ", el("kbd", {text: "→"})])
    ]));
    stage.appendChild(el("div", {"class": "keys", text: "Tip: use the keys Y, N and → to move fast. Nothing is final until you confirm."}));
  } else {
    stage.appendChild(el("div", {"class": "decide"}, [
      el("div", {"class": "callout" + (m.status === "stolen" ? "" : ""), style: "grid-column:1 / 3"}, [el("b", {text: m.status === "stolen" ? "Marked as stolen " : "Marked as not a match "}), "on " + when(m.status_at, true) + "."]),
      el("button", {"class": "btn", type: "button", text: "Undo", onclick: function () { act("/api/status", {ids: [m.id], status: "new"}, "Back in your review list"); }})
    ]));
  }
  main.appendChild(el("div", {"class": "review"}, [stage, infoPanel(m)]));
}
function infoPanel(m) {
  var parts = [m.year, m.quarter, m.file_name].filter(Boolean);
  var sib = siblings(m), rep = repeats(m).filter(function (x) { return sib.indexOf(x) < 0; });
  var appr = approvedFor(m.photographer).length;
  var repPages = {}; rep.forEach(function (x) { repPages[pageOf(x)] = x; });
  return el("div", {"class": "card info"}, [
    el("div", {"class": "who"}, [avatar(m.photographer), el("div", {style: "min-width:0"}, [el("b", {text: m.photographer}), el("div", {"class": "crumbs", text: parts.join(" › ")})])]),
    el("div", {"class": "row-between"}, [
      el("div", {style: "display:flex;gap:6px;flex-wrap:wrap"}, [pill(m.category), stPill(m), m.company ? el("span", {"class": "pill p-net", text: m.company}) : null]),
      el("span", {"class": "mono muted", text: m.match_id})
    ]),
    el("div", {}, [el("div", {style: "font-size:12px;color:var(--muted);font-weight:600;margin-bottom:4px", text: "How alike they are"}),
      el("div", {"class": "meter"}, [el("div", {"class": "bar"}, [el("i", {style: "width:" + Math.round((m.clip_sim || 0) * 100) + "%"})]), el("b", {text: Math.round((m.clip_sim || 0) * 100) + "%"})])]),
    el("dl", {"class": "kv"}, [
      el("dt", {text: "Found on"}), el("dd", {"class": "m", text: m.domain || "file on this PC"}),
      el("dt", {text: "Page"}), el("dd", {"class": "m", text: pageOf(m)}),
      el("dt", {text: "First found"}), el("dd", {text: when(m.found_at, true)}),
      m.times_seen > 1 ? el("dt", {text: "Seen again"}) : null, m.times_seen > 1 ? el("dd", {text: plural(m.times_seen - 1, "time") + ", last " + when(m.last_seen, true)}) : null,
      m.file_date ? el("dt", {text: "Photo taken"}) : null, m.file_date ? el("dd", {text: when(m.file_date)}) : null,
      el("dt", {text: "Why it matched"}), el("dd", {text: m.note || CAT[m.category]})
    ]),
    sib.length ? el("div", {"class": "callout"}, [el("b", {text: "Same article on " + plural(sib.length + 1, "site") + ". "}), sib.map(function (x) { return x.domain; }).concat([m.domain]).join(", ") + " look like one company."]) : null,
    Object.keys(repPages).length ? el("div", {"class": "callout"}, [el("b", {text: "Repeat copy: this photo is on " + plural(Object.keys(repPages).length, "other page") + "."}),
      el("ul", {}, Object.keys(repPages).slice(0, 6).map(function (u) { var x = repPages[u]; return el("li", {}, [el("button", {"class": "link", type: "button", text: x.domain + " · " + x.match_id, onclick: function () { go("review", {cur: x.id}); }})]); }))]) : null,
    el("div", {"class": "more"}, [
      m.page_url || /^https?:/i.test(m.image_url) ? el("button", {"class": "btn", type: "button", text: "Open where found ↗", onclick: function () { openUrl(pageOf(m)); }}) : null,
      el("button", {"class": "btn", type: "button", text: "Proof of Discovery (PDF)", onclick: function () { saveProof(m, true); }}),
      el("button", {"class": "btn", type: "button", text: "Show our file", onclick: function () { api("/api/open-path", {finding: m.id, which: "ours", select: true}).then(function (r) { if (r.ok === false) toast(r.error); }); }}),
      m.page_url && !m.licensed ? el("button", {"class": "btn", type: "button", text: "Approve for " + m.photographer.split(" ")[0] + "…", onclick: function () { openApprove(m.photographer, m.domain, m.sister_sites); }}) : null,
      m.page_url ? el("button", {"class": "btn danger", type: "button", text: "Ban site…", onclick: function () { openBan(m.domain, m.sister_sites); }}) : null
    ]),
    appr ? el("div", {"class": "keys", text: plural(appr, "site") + " approved for " + m.photographer + "."}) : null
  ]);
}
function nextAfter(m) {
  var q = queue().filter(function (x) { return x.id !== m.id; });
  var after = q.filter(function (x) { return RANK[x.category] > RANK[m.category] || (RANK[x.category] === RANK[m.category] && x.id > m.id); })[0];
  return after ? after.id : (q[0] ? q[0].id : null);
}
function decideNo(m) { var n = nextAfter(m); act("/api/status", {ids: [m.id], status: "not_match"}, "Marked as not a match").then(function () { S.cur = n; render(); }); }
function skip(m) { var n = nextAfter(m); if (n === null || n === m.id) { toast("That was the last one"); return; } S.cur = n; render(); }

/* ---------- PHOTOGRAPHERS ---------- */
function photographersView(main) {
  var list = photographers();
  main.appendChild(el("div", {"class": "head"}, [el("div", {}, [el("h1", {text: "Photographers"}), el("p", {text: "Read from your folders: photographer, then year, then quarter."})])]));
  if (!list.length) { main.appendChild(el("div", {"class": "empty", text: "No photographers yet. Add your images in My images."})); return; }
  if (!S.ph || !list.some(function (p) { return p.name === S.ph; })) {
    var withHits = list.slice().sort(function (a, b) { return count(b.name) - count(a.name); });
    S.ph = withHits[0].name; S.period = "all";
  }
  function count(name) { return F().filter(function (m) { return shown(m) && m.photographer === name; }).length; }
  var left = el("div", {"class": "plist"});
  list.forEach(function (p) {
    var ms = F().filter(function (m) { return shown(m) && m.photographer === p.name; }), nw = ms.filter(isOpen).length;
    left.appendChild(el("button", {"class": "pcard", type: "button", "aria-current": String(S.ph === p.name), onclick: function () { S.ph = p.name; S.period = "all"; render(); }}, [
      avatar(p.name), el("div", {"class": "meta"}, [el("b", {text: p.name}), el("span", {text: plural(p.images || 0, "image") + " · " + plural(ms.length, "match", "matches")})]),
      nw ? el("span", {"class": "n", text: nw + " new"}) : null
    ]));
  });
  var mine = F().filter(function (m) { return shown(m) && m.photographer === S.ph; });
  var periods = []; mine.forEach(function (m) { var k = [m.year, m.quarter].filter(Boolean).join(" ") || "No date"; if (periods.indexOf(k) < 0) periods.push(k); });
  periods.sort().reverse();
  var chips = el("div", {"class": "chips"}, [el("button", {"class": "chip", type: "button", "aria-pressed": String(S.period === "all"), text: "All years", onclick: function () { S.period = "all"; render(); }})]
    .concat(periods.map(function (p) { return el("button", {"class": "chip", type: "button", "aria-pressed": String(S.period === p), text: p, onclick: function () { S.period = p; render(); }}); })));
  var ms = mine.filter(function (m) { return S.period === "all" || ([m.year, m.quarter].filter(Boolean).join(" ") || "No date") === S.period; })
    .sort(function (a, b) { return (a.status === "new" ? 0 : 1) - (b.status === "new" ? 0 : 1) || b.id - a.id; });
  var appr = approvedFor(S.ph);
  var stolen = mine.filter(function (m) { return m.status === "stolen"; }).length, open = mine.filter(isOpen).length;
  var right = el("div", {style: "display:flex;flex-direction:column;gap:14px;min-width:0"}, [
    el("div", {"class": "row-between"}, [el("div", {}, [el("h2", {"class": "section-title", text: S.ph}), el("div", {"class": "muted", style: "font-size:13px", text: plural(mine.length, "match", "matches") + " · " + open + " to review · " + stolen + " confirmed stolen"})]),
      el("button", {"class": "btn", type: "button", text: "Approve a site for " + S.ph.split(" ")[0] + "…", onclick: function () { openApprove(S.ph, "", []); }})]),
    periods.length ? chips : null,
    appr.length ? el("div", {"class": "callout ok"}, [el("b", {text: "Approved sites for " + S.ph + ": "}), appr.join(", ") + ". Matches there show as Licensed."]) : null,
    grid(ms, "No matches for " + S.ph + " yet.")
  ]);
  main.appendChild(el("div", {"class": "split"}, [left, right]));
}

/* ---------- SITES ---------- */
function sitesView(main) {
  main.appendChild(el("div", {"class": "head"}, [el("div", {}, [el("h1", {text: "Sites"}), el("p", {text: "Every site where your images turned up. Sites that look like one company are grouped."})]),
    el("button", {"class": "btn", type: "button", text: "Import site list", onclick: openImport})]));
  var doms = {};
  F().forEach(function (m) { if (!shown(m) || !m.page_url) return; (doms[m.domain] = doms[m.domain] || []).push(m); });
  if (!Object.keys(doms).length) { main.appendChild(el("div", {"class": "empty", text: "No sites yet. Matches from pages you check show up here."})); return; }
  var nets = {}, solo = [];
  Object.keys(doms).forEach(function (d) { var c = doms[d][0].company; if (c) (nets[c] = nets[c] || []).push(d); else solo.push(d); });
  var tb = el("tbody");
  function row(d, sub) {
    var ms = doms[d], phs = {}; ms.forEach(function (m) { phs[m.photographer] = 1; });
    var nw = ms.filter(isOpen).length, st = ms.filter(function (m) { return m.status === "stolen"; }).length, lic = ms.filter(function (m) { return m.licensed; }).length;
    function pickIt() { S.site = S.site === d ? null : d; render(); }
    return el("tr", {"class": "click" + (sub ? " sub" : "") + (S.site === d ? " sel" : ""), tabindex: "0", onclick: pickIt, onkeydown: function (e) { if (e.key === "Enter") pickIt(); }}, [
      el("td", {}, [el("span", {"class": "dom", text: d})]),
      el("td", {"class": "n", text: String(ms.length)}), el("td", {"class": "n", text: nw ? String(nw) : "–"}), el("td", {"class": "n", text: st ? String(st) : "–"}),
      el("td", {}, [el("div", {"class": "avs"}, Object.keys(phs).map(function (p) { return avatar(p, true); }))].concat(lic ? [el("span", {"class": "pill p-ok", style: "margin-top:4px", text: lic + " licensed"})] : [])),
      el("td", {style: "text-align:right;white-space:nowrap"}, [
        el("button", {"class": "btn small", type: "button", text: "Open ↗", onclick: function (e) { e.stopPropagation(); openUrl("https://" + d); }}), " ",
        el("button", {"class": "btn small danger", type: "button", text: "Ban…", onclick: function (e) { e.stopPropagation(); openBan(d, doms[d][0].sister_sites); }})])
    ]);
  }
  Object.keys(nets).sort().forEach(function (n) {
    tb.appendChild(el("tr", {"class": "netrow"}, [el("td", {colspan: "6", text: n + " · " + plural(nets[n].length, "site") + " with your images, probably one owner"})]));
    nets[n].sort().forEach(function (d) { tb.appendChild(row(d, true)); });
  });
  solo.sort(function (a, b) { return doms[b].length - doms[a].length; }).forEach(function (d) { tb.appendChild(row(d, false)); });
  main.appendChild(el("div", {"class": "tablewrap"}, [el("table", {}, [el("thead", {}, [el("tr", {}, ["Site", "Matches", "To review", "Stolen", "Photographers", ""].map(function (h) { return el("th", {text: h}); }))]), tb])]));
  if (S.site && doms[S.site]) { main.appendChild(el("h2", {"class": "section-title", text: "Your images on " + S.site})); main.appendChild(grid(doms[S.site])); }
}

/* ---------- SITE LISTS ---------- */
function listsView(main) {
  var d = S.data;
  main.appendChild(el("div", {"class": "head"}, [el("div", {}, [el("h1", {text: "Site lists"}), el("p", {text: "These lists decide what PixelProof hides, what counts as licensed, and which sites belong together."})]),
    el("button", {"class": "btn ink", type: "button", text: "Import site list", onclick: openImport})]));
  function addRow(placeholder, onAdd, extra) {
    var input = el("input", {type: "text", placeholder: placeholder});
    function doIt() { var v = input.value.trim(); if (!v) return; onAdd(v, extra ? extra.value : null); }
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") doIt(); });
    return el("div", {"class": "add"}, [extra, input, el("button", {"class": "btn", type: "button", text: "Add", onclick: doIt})]);
  }
  function lc(title, desc, items, add) {
    return el("div", {"class": "card lcard"}, [el("h3", {text: title + " (" + items.length + ")"}), el("p", {text: desc}),
      items.length ? el("ul", {}, items) : el("p", {html: "<i>Empty.</i>"}), add]);
  }
  function li(label, onRemove, sub) {
    return el("li", {}, [el("span", {}, [el("span", {"class": "mono", text: label}), sub ? el("span", {"class": "muted", text: "  " + sub}) : null]),
      onRemove ? el("button", {"class": "x", type: "button", "aria-label": "Remove " + label, title: "Remove", text: "×", onclick: onRemove}) : null]);
  }
  var who = el("select", {"aria-label": "Photographer"}, photographers().map(function (p) { return el("option", {value: p.name, text: p.name}); }));
  var approvedItems = [];
  d.approved.forEach(function (a) { a.sites.forEach(function (s) { approvedItems.push(li(s, function () { act("/api/approve", {photographer: a.photographer, sites: [s], remove: true}, "Removed"); }, "→ " + a.photographer)); }); });
  var sisterItems = d.sisters.map(function (g) {
    return li(g.sites.join(", "), g.named ? function () { act("/api/sister", {name: g.name, remove: true}, "Removed"); } : null, g.named ? "· " + g.name : "· found by itself");
  });
  main.appendChild(el("div", {"class": "lists"}, [
    lc("Banned for everyone", "Your own sites and full partners. Their matches are hidden for every photographer.",
      d.banned.map(function (s) { return li(s, function () { act("/api/ban", {sites: [s], remove: true}, s + " is no longer banned"); }); }),
      addRow("site to ban, like mysite.com", function (v) { act("/api/ban", {sites: [v]}, "Banned"); })),
    lc("Approved for one photographer", "A site that licensed one photographer's work. Only their matches there count as Licensed.",
      approvedItems, addRow("site, like nypost.com", function (v, ph) { act("/api/approve", {photographer: ph, sites: [v]}, "Approved for " + ph); }, who)),
    lc("Sister sites (one company)", "PixelProof groups sites by itself when they share the same article links. You can also name a company here.",
      sisterItems, sisterAdd()),
    lc("Sites to watch", "Sites known for using images without permission. The crawler (coming next) will visit these.",
      d.watch.map(function (s) { return li(s, function () { act("/api/watch", {sites: [s], remove: true}, "Removed"); }); }),
      addRow("site to watch", function (v) { act("/api/watch", {sites: [v]}, "Added to the watch list"); }))
  ]));
  function sisterAdd() {
    var name = el("input", {type: "text", placeholder: "Company name"}), sites = el("input", {type: "text", placeholder: "site1.com, site2.com"});
    return el("div", {"class": "add"}, [name, sites, el("button", {"class": "btn", type: "button", text: "Add", onclick: function () {
      act("/api/sister", {name: name.value, sites: sites.value.split(/[,;\s]+/)}, "Company added");
    }})]);
  }
}

/* ---------- MY IMAGES ---------- */
function libView(main) {
  var lib = S.data.library;
  main.appendChild(el("div", {"class": "head"}, [el("div", {}, [el("h1", {text: "My images"}), el("p", {text: lib.images.toLocaleString() + " images fingerprinted · " + plural(lib.pages, "page") + " checked so far"})])]));
  var input = el("input", {type: "text", placeholder: "C:\\Photos\\Photographers", "aria-label": "Folder"});
  main.appendChild(el("div", {"class": "card addfolder"}, [
    el("h2", {text: "Add a folder of images"}),
    el("p", {text: "Pick your Photographers folder (or any folder inside it). PixelProof reads photographer, year and quarter from the folder names. You can stop any time; next time it carries on where it stopped and skips images it already knows."}),
    el("div", {"class": "urlrow"}, [input,
      el("button", {"class": "btn", type: "button", text: "Browse…", onclick: function () { api("/api/pick", {kind: "folder"}).then(function (r) { if (r.ok === false) return toast(r.error); if (r.path) input.value = r.path; }); }}),
      el("button", {"class": "btn ink", type: "button", text: "Start fingerprinting", onclick: function () {
        var v = input.value.trim(); if (!v) return toast("Pick a folder first");
        api("/api/index", {folder: v}).then(function (r) { if (r.ok === false) return toast(r.error); pollJob(); });
      }})])
  ]));
  var ps = lib.photographers;
  if (!ps.length) { main.appendChild(el("div", {"class": "empty", text: "No images yet."})); return; }
  var tb = el("tbody");
  ps.slice().sort(function (a, b) { return b.images - a.images; }).forEach(function (p) {
    var ms = F().filter(function (m) { return shown(m) && m.photographer === p.name; });
    tb.appendChild(el("tr", {"class": "click", tabindex: "0", onclick: function () { go("ph", {ph: p.name, period: "all"}); }}, [
      el("td", {}, [el("div", {style: "display:flex;gap:10px;align-items:center"}, [avatar(p.name, true), el("b", {text: p.name})])]),
      el("td", {"class": "n", text: p.images.toLocaleString()}),
      el("td", {text: p.y1 ? (p.y1 === p.y2 ? String(p.y1) : p.y1 + "–" + p.y2) : "–"}),
      el("td", {"class": "n", text: String(ms.length)})
    ]));
  });
  main.appendChild(el("div", {"class": "tablewrap"}, [el("table", {}, [el("thead", {}, [el("tr", {}, ["Photographer", "Images", "Years", "Matches"].map(function (h) { return el("th", {text: h}); }))]), tb])]));
  if (lib.errors) main.appendChild(el("p", {"class": "muted", text: plural(lib.errors, "file") + " could not be read (damaged or not really pictures)."}));
}

/* ---------- dialogs ---------- */
var openDlg = null;
function closeDlg() { if (openDlg) { openDlg.remove(); openDlg = null; } }
function dialog(title, body, buttons, small) {
  closeDlg();
  var d = el("div", {"class": "dialog" + (small ? " small" : ""), role: "dialog", "aria-modal": "true", "aria-label": title}, [el("h3", {text: title}), el("div", {"class": "dbody"}, body), el("div", {"class": "dfoot"}, buttons)]);
  var o = el("div", {"class": "overlay", onclick: function (e) { if (e.target === o) closeDlg(); }}, [d]);
  document.body.appendChild(o); openDlg = o;
  var f = d.querySelector(".dfoot .btn:last-child"); if (f) f.focus();
}
function cancel(t) { return el("button", {"class": "btn", type: "button", text: t || "Cancel", onclick: closeDlg}); }
function proofSheet(m) {
  function row(k, v, mono) { return v ? el("tr", {}, [el("td", {text: k}), el("td", {"class": mono ? "m" : null, text: v})]) : null; }
  var sib = siblings(m);
  return el("div", {"class": "proof"}, [
    el("div", {"class": "ph"}, [el("div", {}, [el("h4", {text: "Proof of Discovery"}), el("div", {style: "font-size:11px;opacity:.75", text: "PixelProof image use record"})]), el("span", {"class": "id", text: m.match_id})]),
    el("div", {"class": "imgs"}, [
      el("figure", {"class": "o"}, [el("div", {}, [el("img", {src: pic("ours", m.image_id, 900), alt: "Our image"})]), el("figcaption", {text: "Ours: " + m.file_name})]),
      el("figure", {"class": "t"}, [el("div", {}, [el("img", {src: pic("theirs", m.id, 900), alt: "Their image"})]), el("figcaption", {text: "Theirs: as found on " + (m.domain || "-")})])
    ]),
    el("table", {}, [el("tbody", {}, [
      row("Infringing URL", pageOf(m), 1), row("Website", m.domain, 1), row("Date and time found", when(m.found_at, true)), row("Match ID", m.match_id, 1),
      row("Photographer", m.photographer), row("Our image", m.our_path, 1), row("Match type", CAT[m.category] + " · " + Math.round((m.clip_sim || 0) * 100) + "% alike"),
      sib.length ? row("Same article also on", sib.map(function (x) { return x.domain; }).join(", "), 1) : null,
      row("Their copy saved", m.has_evidence ? "Yes, with SHA-256 fingerprint" : "Small preview only (found before evidence saving)")
    ])])
  ]);
}
function saveProof(m, open) {
  toast("Making the PDF…");
  return api("/api/proof", {id: m.id, open: open}).then(function (r) {
    if (r.ok === false) { toast(r.error); return r; }
    toast("Saved " + r.name + (open ? ", opening it" : "")); refresh(); return r;
  });
}
function openConfirm(m) {
  var sib = siblings(m).filter(function (x) { return x.status === "new" && !x.banned; });
  var also = sib.length ? el("input", {type: "checkbox", id: "also-sib", checked: true}) : null;
  var step1 = el("li", {text: "Look over the proof and save the PDF."});
  dialog("Confirm this image is stolen", [
    el("ol", {"class": "steps"}, [step1, el("li", {text: "Confirm again to mark it as stolen."})]),
    proofSheet(m),
    also ? el("label", {"class": "checkline"}, [also, el("span", {text: "Also mark the same photo on its sister sites (" + sib.map(function (x) { return x.domain; }).join(", ") + ")"})]) : null
  ], [cancel(),
    el("button", {"class": "btn", type: "button", text: "Save proof (PDF)", onclick: function () { saveProof(m, true).then(function (r) { if (r.ok !== false) { step1.className = "done"; step1.textContent = "Proof saved: " + r.name; } }); }}),
    el("button", {"class": "btn good", type: "button", text: "Yes, it's stolen. Mark it.", onclick: function () {
      var ids = [m.id].concat(also && also.checked ? sib.map(function (x) { return x.id; }) : []);
      var n = nextAfter(m); closeDlg();
      api("/api/status", {ids: ids, status: "stolen"}).then(function (r) {
        if (r.ok === false) return toast(r.error);
        /* refresh the saved PDFs so they say "Confirmed stolen" */
        Promise.all(ids.map(function (id) { return api("/api/proof", {id: id, open: false}); })).then(function () {
          toast(m.match_id + " marked as stolen. Proof saved in your proofs folder."); S.cur = n; refresh();
        });
      });
    }})]);
}
function sisterBox(site, sisters, verb) {
  var others = (sisters || []).filter(function (d) { return d !== site; });
  if (!others.length) return [null, null];
  var box = el("input", {type: "checkbox", checked: true});
  return [el("label", {"class": "checkline"}, [box, el("span", {text: verb + " its sister sites too: " + others.join(", ")})]), function () { return box.checked ? others : []; }];
}
function openBan(site, sisters) {
  var n = F().filter(function (m) { return m.domain === site; }).length;
  var sb = sisterBox(site, sisters, "Ban");
  dialog("Ban " + site + "?", [
    el("p", {text: "This hides " + plural(n, "match", "matches") + " from " + site + " for every photographer. Pages on this site will not be checked any more."}),
    el("p", {"class": "muted", text: "Use this for your own sites and full partners. You can undo it in Site lists."}), sb[0]
  ], [cancel(), el("button", {"class": "btn ink", type: "button", text: "Yes, ban this site", onclick: function () {
    var list = [site].concat(sb[1] ? sb[1]() : []); closeDlg();
    act("/api/ban", {sites: list}, plural(list.length, "site") + " banned").then(function () { if (S.view === "review") { S.cur = null; render(); } });
  }})], true);
}
function openApprove(ph, site, sisters) {
  var input = el("input", {type: "text", value: site || "", placeholder: "site, like nypost.com"});
  var sb = sisterBox(site, sisters, "Approve");
  dialog(site ? "Approve " + site + " for " + ph + "?" : "Approve a site for " + ph, [
    el("p", {text: "Use this when a site has licensed " + ph + "'s work. Their matches there will show as Licensed instead of stolen."}),
    el("p", {"class": "muted", text: "Other photographers' images on that site still count as stolen."}),
    site ? null : input, sb[0]
  ], [cancel(), el("button", {"class": "btn ink", type: "button", text: "Yes, approve for " + ph, onclick: function () {
    var v = (site || input.value).trim(); if (!v) return toast("Type the site first");
    var list = [v].concat(sb[1] ? sb[1]() : []); closeDlg();
    act("/api/approve", {photographer: ph, sites: list}, "Approved for " + ph).then(function () { if (S.view === "review") { S.cur = null; render(); } });
  }})], true);
}
function openImport() {
  var ta = el("textarea", {placeholder: "badsite1.com\nhttps://www.badsite2.net/gallery\nbadsite3.org"});
  var file = el("input", {type: "file", accept: ".txt,.csv"});
  file.addEventListener("change", function () { var f = file.files[0]; if (!f) return; var rd = new FileReader(); rd.onload = function () { ta.value = String(rd.result); }; rd.readAsText(f); });
  var rW = el("input", {type: "radio", name: "into", value: "watch", checked: true}), rB = el("input", {type: "radio", name: "into", value: "ban"});
  dialog("Import a site list", [
    el("p", {text: "Paste sites or links (one per line), or choose a .txt or .csv file. PixelProof keeps just the site name."}),
    file, ta,
    el("div", {"class": "radio"}, [el("label", {}, [rW, "Add to Sites to watch"]), el("label", {}, [rB, "Add to Banned for everyone"])])
  ], [cancel(), el("button", {"class": "btn ink", type: "button", text: "Import", onclick: function () {
    api("/api/import", {text: ta.value, into: rB.checked ? "ban" : "watch"}).then(function (r) {
      if (r.ok === false) return toast(r.error); closeDlg(); toast("Added " + plural(r.added, "site")); refresh();
    });
  }})]);
}

/* ---------- jobs (checking a page, fingerprinting) ---------- */
var jobTimer = null, lastJobState = null;
function startCheck(targets) {
  api("/api/check", {targets: targets}).then(function (r) { if (r.ok === false) return toast(r.error); pollJob(); });
}
function pollJob() {
  clearTimeout(jobTimer);
  api("/api/job").then(function (j) {
    renderJob(j);
    if (j && j.state === "running") { jobTimer = setTimeout(pollJob, 900); lastJobState = "running"; }
    else if (lastJobState === "running") { lastJobState = j ? j.state : null; refresh(); }
  });
}
function renderJob(j) {
  var box = document.getElementById("job");
  if (!j || j.dismissed) { box.hidden = true; return; }
  box.hidden = false; box.innerHTML = "";
  var running = j.state === "running";
  var title = j.kind === "check" ? (running ? "Checking " + j.label : "Checked " + j.label) : (running ? "Fingerprinting your images" : "Fingerprinting finished");
  if (j.state === "error") title = "That did not work";
  if (j.state === "stopped") title = "Stopped";
  var last = j.lines.length ? j.lines[j.lines.length - 1] : "Starting…";
  var pct = j.total ? Math.round(j.done / j.total * 100) : 0;
  var kids = [el("div", {"class": "top"}, [el("span", {"class": running ? "spin" : j.state === "done" ? "ok" : "err"}), el("b", {text: title}),
    running ? null : el("button", {"class": "x", type: "button", "aria-label": "Close", text: "×", onclick: function () { box.hidden = true; }})])];
  if (running) {
    kids.push(el("div", {"class": "bar" + (j.total ? "" : " indet")}, [el("i", {style: "width:" + pct + "%"})]));
    if (j.kind === "index" && j.total) kids.push(el("div", {"class": "line", text: j.done.toLocaleString() + " of " + j.total.toLocaleString() + " new images (" + pct + "%)"}));
  }
  kids.push(el("div", {"class": "line", text: last}));
  kids.push(el("details", {}, [el("summary", {text: "Details"}), el("pre", {text: j.lines.join("\n")})]));
  var acts = el("div", {"class": "acts"});
  if (running) acts.appendChild(el("button", {"class": "btn small", type: "button", text: "Stop", onclick: function () { api("/api/stop", {}); toast("Stopping…"); }}));
  var res = j.result || {};
  if (!running && j.kind === "check" && res.banned) {
    kids.push(el("div", {"class": "line", style: "color:var(--ink)", text: res.domain + " is on your ban list, so it was not checked."}));
  } else if (!running && j.kind === "check" && res.ids && res.ids.length) {
    var ids = res.new_ids && res.new_ids.length ? res.new_ids : res.ids;
    var fresh = (res.new_ids || []).length, old = res.ids.length - fresh;
    kids.push(el("div", {"class": "line", style: "color:var(--ink);font-weight:600", text:
      (fresh ? plural(fresh, "new match", "new matches") : "No new matches") + (old ? " · " + plural(old, "match", "matches") + " found before" : "")}));
    acts.appendChild(el("button", {"class": "btn ink small", type: "button", text: fresh ? "Review " + plural(fresh, "new match", "new matches") : "Show them", onclick: function () { box.hidden = true; go("review", {cur: ids[0]}); }}));
  } else if (!running && j.kind === "check" && j.state === "done") {
    kids.push(el("div", {"class": "line", style: "color:var(--ink);font-weight:600", text: "None of your images are on that page."}));
  }
  if (acts.childNodes.length) kids.push(acts);
  kids.forEach(function (k) { if (k) box.appendChild(k); });
}

/* ---------- main render ---------- */
function render() {
  if (!S.data) return;
  renderNav();
  var main = document.getElementById("main"); main.innerHTML = "";
  ({home: home, review: review, ph: photographersView, sites: sitesView, lists: listsView, lib: libView}[S.view] || home)(main);
}

document.addEventListener("keydown", function (e) {
  if (openDlg) { if (e.key === "Escape") closeDlg(); return; }
  if (S.view !== "review" || /INPUT|TEXTAREA|SELECT/.test((e.target || {}).tagName || "")) return;
  var q = queue(), m = S.cur ? byId(S.cur) : q[0];
  if (!m || m.status !== "new" || m.licensed) return;
  if (e.key === "y" || e.key === "Y") { e.preventDefault(); openConfirm(m); }
  else if (e.key === "n" || e.key === "N") { e.preventDefault(); decideNo(m); }
  else if (e.key === "ArrowRight") { e.preventDefault(); skip(m); }
  else if (e.key === "s" || e.key === "S") { S.mode = S.mode === "side" ? "swipe" : "side"; render(); }
});

/* keep the engine running while this window is open; tell it when the window closes */
setInterval(function () { api("/api/ping"); }, 4000);
window.addEventListener("pagehide", function () { try { navigator.sendBeacon("/api/bye?t=" + encodeURIComponent(TOKEN), "{}"); } catch (e) {} });
window.PixelProof = {go: go, state: function () { return S; }};

refresh().then(function () { pollJob(); });
})();
