/* =============================================================================
   Sahara — operator console
   -----------------------------------------------------------------------------
   One file, no build step, no dependencies. Structure:

     api / fmt      thin fetch wrapper and formatters
     state          the last response from each endpoint, nothing derived
     render*        one function per view, each a pure function of state
     control room   POST + poll the job endpoints in app/control.py
     router         hash routing, so every view is a shareable link

   Two rules this file follows:

   1. It never restates a rule the backend owns. Policy cells, invariant text,
      category lists and bounds are all rendered from /api/mechanism. If the console
      shows a rule, the code enforcing it produced the words.
   2. Every value that reaches innerHTML goes through esc() first. Case ids,
      subscription ids and error descriptions arrive from a webhook body — that is
      attacker-influenced text, and it is rendered escaped, never raw. Subprocess
      output goes to textContent, never innerHTML.
============================================================================= */
"use strict";

/* --------------------------------------------------------------------- api */
async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  const body = await res.text();
  let data = null;
  try { data = body ? JSON.parse(body) : null; } catch (_) { data = { detail: body }; }
  if (!res.ok) {
    const err = new Error((data && (data.detail || data.message)) || (res.status + " " + res.statusText));
    err.status = res.status;
    throw err;
  }
  return data;
}

const post = (path, payload) => api(path, { method: "POST", body: JSON.stringify(payload || {}) });

/* -------------------------------------------------------------------- fmt */
const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const INR2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const rupees = (paise) => "₹" + INR.format(Math.round((paise || 0) / 100));
const rupees2 = (rs) => "₹" + INR2.format(rs || 0);
const pct = (x, digits) => (100 * (x || 0)).toFixed(digits === undefined ? 1 : digits) + "%";
const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const words = (s) => String(s || "").replace(/_/g, " ");
const icon = (name, cls) => '<svg class="icon ' + (cls || "") + '"><use href="#i-' + name + '"/></svg>';

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleString(undefined, {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function hours(h) {
  if (h === null || h === undefined) return "—";
  if (h < 1) return Math.round(h * 60) + " min";
  if (h < 48) return h.toFixed(1) + " h";
  return (h / 24).toFixed(1) + " d";
}

/* status -> visual role. Colour is never the only signal: the label ships with it. */
const STATUS_ROLE = {
  recovered: "ok",
  open: "accent",
  stopped_handoff: "warn",
  stopped_unknown: "warn",
  stopped_opt_out: "danger",
  stopped_max_attempts: "danger",
  stopped_cooldown_expired: "warn",
  stopped_holdout: "control",
};
const statusBadge = (s) => '<span class="badge ' + (STATUS_ROLE[s] || "") + '">' + esc(words(s)) + "</span>";

const ACTION_ROLE = {
  RETRY_LATER: "accent", SEND_UPDATE_LINK: "ok", PROMISE_TO_PAY: "warn", STOP_HANDOFF: "",
};

const COLOUR = {
  ok: "var(--ok)", accent: "var(--accent)", warn: "var(--warn)",
  danger: "var(--danger)", control: "var(--control)", "": "var(--neutral)",
};

/* ------------------------------------------------------------------ state */
const state = {
  summary: null, mechanism: null, categories: null, health: null,
  cases: [], control: null, job: null, jobTimer: null, jobCursor: 0,
  loading: false,
  sort: { key: "updated_at", dir: "descending" },
  lastFocus: null,   // what to hand focus back to when the drawer closes
};

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

/* ----------------------------------------------------------------- toasts */
function toast(text, kind) {
  const el = document.createElement("div");
  el.className = "toast " + (kind || "info");
  el.innerHTML = icon(kind === "err" ? "warn" : kind === "ok" ? "check" : "info") +
    "<span>" + esc(text) + "</span>";
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);   // 3-5s: long enough to read, short enough not to nag
}

const _toasted = {};
function toastOnce(key, text, kind) {
  if (_toasted[key]) return;
  _toasted[key] = true;
  toast(text, kind);
}

/* ------------------------------------------------------------------ views */
/* Five destinations. Where two pages answered halves of one question they became
   tabs inside a single view, addressed as #/view:tab — so the URL still points at
   exactly what is on screen, and the rail stays short enough to read at a glance.
   Each entry carries the one question its view answers; the topbar prints it. */
const VIEWS = {
  overview:  {
    title: "Overview",
    q: "Did this recover money, and where did every case end up?",
  },
  results:   {
    title: "Results",
    subs: {
      impact: { title: "Incremental impact",
                q: "How much of the recovery would not have happened anyway?" },
      causes: { title: "By failure cause", q: "Which failures are actually recoverable?" },
    },
  },
  mechanism: {
    title: "How it works",
    subs: {
      pipeline:   { title: "Pipeline", q: "What did the loop do, counted in rows?" },
      guardrails: { title: "Guardrails",
                    q: "What can this agent never do, and how often did that bind?" },
      policy:     { title: "Decision policy", q: "How is the single intervention chosen?" },
      model:      { title: "Model boundary", q: "Where is the model — and where is it not?" },
    },
  },
  cases:     {
    title: "Cases",
    q: "Every case, and the complete file behind any one of them.",
  },
  control:   {
    title: "Control room",
    q: "Run the suite, replay the batch, and attack the running server.",
  },
  livedemo:  {
    title: "Test mode",
    q: "Fail a real Razorpay payment and watch the pipeline react live.",
  },
};

const firstSub = (name) => Object.keys(VIEWS[name].subs || {})[0] || null;

/* ============================================================== rendering */

function renderChips() {
  const h = state.health || {};
  const c = state.control || {};
  const s = state.summary || {};
  const chips = [];

  chips.push('<span class="chip ' + (h.status === "ok" ? "live" : "danger") +
    '"><span class="dot"></span>API ' + esc(h.status || "down") + "</span>");

  const provider = h.llm_provider || "none";
  chips.push('<span class="chip ' + (provider === "none" ? "warn" : "ok") +
    '"><span class="dot"></span>model: ' + esc(provider) + "</span>");

  chips.push('<span class="chip ' + (h.razorpay_configured ? "ok" : "") +
    '"><span class="dot"></span>razorpay ' + (h.razorpay_configured ? "test keys" : "not configured") + "</span>");

  if (c.live_link_budget !== undefined) {
    chips.push('<span class="chip"><span class="dot"></span>live links left: ' + esc(c.live_link_budget) + "</span>");
  }
  $("#chips").innerHTML = chips.join("");

  const split = s.n_synthetic !== undefined
    ? s.n_synthetic + " synthetic · " + s.n_live + " live"
    : "no cases yet";
  $("#rail-note").innerHTML = esc(split) + "<br>db: <code>" + esc(h.db_path || "recovery.db") + "</code>";

  $("#nav-cases").textContent = state.cases.length ? state.cases.length : "";
  const guards = state.mechanism ? state.mechanism.invariants.reduce((a, i) => a + i.stops, 0) : 0;
  $("#nav-mechanism").textContent = guards ? guards + " stops" : "";
  const inc = s.incremental;
  $("#nav-results").textContent = inc && inc.available ? pct(inc.lift, 0) + " lift" : "";
}

function renderBanner() {
  const s = state.summary, h = state.health, el = $("#banner");
  if (!s || !h) { el.className = "banner"; return; }
  const notes = [];
  if (h.llm_provider === "none") {
    notes.push("No model is configured, so every case the rules cannot reach stops as <code>unknown</code> " +
      "instead of being guessed. Both behaviours are correct; this is the conservative one.");
  }
  if ((s.n_synthetic || 0) + (s.n_live || 0) === 0) {
    notes.push('The database is empty. Replay the batch from the <a href="#/control">control room</a> to populate it.');
  }
  if (s.reconciliation && !(s.reconciliation.counts_balance && s.reconciliation.amounts_balance)) {
    notes.push("Reconciliation does not balance — counts or amounts disagree with the case table.");
  }
  if (!notes.length) { el.className = "banner"; el.innerHTML = ""; return; }
  el.className = "banner show" + (notes.length > 1 ? " warn" : "");
  el.innerHTML = icon("info") + "<span>" + notes.join(" ") + "</span>";
}

/* -------------------------------------------------------------- overview */
function renderOverview() {
  const s = state.summary;
  if (!s) return;
  const rate = s.recovery_rate || {};
  const inc = s.incremental || {};
  const net = s.net || {};

  const tiles = [
    { k: "₹ at risk", v: rupees2(s.total_at_risk_rupees), cls: "",
      s: (rate.strict_denominator || 0) + " cases entered the pipeline", trace: "at_risk" },
    { k: "₹ recovered", v: rupees2(s.total_recovered_rupees), cls: "ok",
      s: (s.n_recovered || 0) + " cases confirmed by a recovery signal", trace: "recovered" },
    { k: "Recovery rate", v: pct(rate.rate), cls: "ok",
      s: (rate.numerator || 0) + "/" + (rate.denominator || 0) + " treated · strict " +
         pct(rate.strict_rate) + " over all " + (rate.strict_denominator || 0) },
    { k: "Incremental lift", v: inc.available ? pct(inc.lift) : "n/a", cls: "control",
      s: inc.available
        ? "vs a " + ((inc.control && inc.control.n) || 0) + "-case untouched control arm" +
          (inc.significant ? " · significant" : " · not significant")
        : "no control arm in this run" },
    { k: "Net of cost", v: net.incremental_available
        ? rupees(net.net_incremental_paise) : rupees(net.net_recovered_paise), cls: "control",
      s: net.incremental_available
        ? "incremental recovery minus every rupee spent to get it"
        : "gross recovery minus " + rupees(net.total_cost_paise) + " of outreach and handoff" },
    { k: "Avg time to recovery", v: hours(s.avg_time_to_recovery_hours), cls: "",
      s: esc(s.time_basis || "") },
    { k: "Stopped", v: String((s.stopped && s.stopped.total) || 0), cls: "warn",
      s: "each one has a complete case file to hand off", trace: "stopped" },
  ];

  $("#tiles").innerHTML = tiles.map((t) =>
    '<div class="tile ' + t.cls + '">' +
      '<div class="tile-k">' + esc(t.k) + "</div>" +
      '<div class="tile-v">' + esc(t.v) + "</div>" +
      '<div class="tile-s">' + t.s + "</div>" +
      (t.trace ? '<a class="trace" href="#" data-trace="' + esc(t.trace) + '">' +
        icon("trace") + "trace the case ids</a>" : "") +
    "</div>").join("");

  /* ---- where every case ended */
  const by = (s.stopped && s.stopped.by_status) || {};
  const rows = [["recovered", s.n_recovered || 0, "ok"], ["open", s.n_open || 0, "accent"]]
    .concat(Object.keys(by).filter((k) => by[k] > 0).map((k) => [k, by[k], STATUS_ROLE[k] || "warn"]))
    .filter((r) => r[1] > 0);

  const total = rows.reduce((a, r) => a + r[1], 0) || 1;
  $("#outcome-n").textContent = total + " cases";
  $("#outcome-split").innerHTML =
    '<div class="split" role="img" aria-label="' +
      esc(rows.map((r) => words(r[0]) + ": " + r[1]).join(", ")) + '">' +
      rows.map((r) => '<span style="width:' + (100 * r[1] / total).toFixed(2) +
        "%;background:" + (COLOUR[r[2]] || COLOUR[""]) + '"></span>').join("") +
    "</div>" +
    '<table class="grid" style="margin-top:12px">' +
      '<thead><tr><th>Outcome</th><th class="num">Cases</th><th class="num">Share</th></tr></thead><tbody>' +
      rows.map((r) => "<tr><td>" + statusBadge(r[0]) + '</td><td class="num">' + r[1] +
        '</td><td class="num">' + pct(r[1] / total) + "</td></tr>").join("") +
    "</tbody></table>";

  $("#effect-mini").innerHTML = inc.available ? armsHtml(inc, true) :
    '<div class="empty">This run has no control arm, so gross recovery is all that can be claimed.<br>' +
    "Replay the batch with a holdout fraction from the control room to measure incrementality.</div>";

  renderFlow($("#flow-mini"), true);
}

/* ---------------------------------------------------------------- impact */
function armsHtml(inc, compact) {
  const t = inc.treated || {}, c = inc.control || {};
  const max = Math.max(t.rate || 0, c.rate || 0, 0.0001);
  const bar = (label, arm, cls) =>
    '<div style="margin-bottom:10px">' +
      '<div style="display:flex;gap:8px;align-items:baseline;font-size:12px">' +
        "<strong>" + esc(label) + "</strong>" +
        '<span class="dim">n=' + (arm.n || 0) + "</span>" +
        '<span style="margin-left:auto">' + pct(arm.rate) +
          ' <span class="dim">(' + (arm.recovered || 0) + " recovered)</span></span>" +
      "</div>" +
      '<div class="bar ' + cls + '" style="margin-top:5px"><span style="width:' +
        (100 * (arm.rate || 0) / max).toFixed(1) + '%"></span></div>' +
    "</div>";

  const lift =
    '<div style="display:flex;gap:22px;flex-wrap:wrap;align-items:flex-end;margin-top:4px">' +
      "<div>" +
        '<div class="tile-k">Lift (treated − control)</div>' +
        '<div class="tile-v" style="font-size:23px">' + pct(inc.lift) + "</div>" +
        '<div class="tile-s">95% CI ' + pct(inc.lift_ci95[0]) + " … " + pct(inc.lift_ci95[1]) +
          " · " + (inc.significant
            ? '<span class="badge ok">significant</span>'
            : '<span class="badge warn">not significant</span>') + "</div>" +
      "</div>" +
      "<div>" +
        '<div class="tile-k">Incremental ₹</div>' +
        '<div class="tile-v" style="font-size:23px">' + rupees(inc.incremental_paise_total) + "</div>" +
        '<div class="tile-s">of ' + rupees(inc.gross_recovered_paise) + " gross · CI " +
          rupees(inc.incremental_paise_ci95[0]) + " … " + rupees(inc.incremental_paise_ci95[1]) + "</div>" +
      "</div>" +
    "</div>";

  return bar("Treated", t, "") + bar("Control (never touched)", c, "control") + lift +
    (compact ? "" : '<p class="foot-note" style="margin-top:14px">' + esc(inc.basis || "") +
      " Bootstrap over " + (inc.bootstrap_samples || 0) + " resamples.</p>");
}

function renderImpact() {
  const inc = state.summary && state.summary.incremental;
  const el = $("#effect-panel");
  if (!inc || !inc.available) {
    el.innerHTML = '<div class="empty">No control arm in this run.<br>' +
      "Replay the batch with <code>holdout &gt; 0</code> from the control room and this becomes measurable.</div>";
    return;
  }
  el.innerHTML =
    '<div class="panel-head"><h2>Treated vs control</h2><span class="spacer"></span>' +
      '<span class="muted">randomised at intake, assignment travels with the event</span></div>' +
    '<div class="panel-body">' + armsHtml(inc, false) + netHtml() + "</div>";
  renderLiftByCategory();
  renderCalibration();
  renderDeclined();
}

/* Lift per cause, with its interval and its arm counts. A row whose interval spans
   zero is shown as spanning zero rather than quietly rounded into a win. */
function renderLiftByCategory() {
  const rows = (state.summary && state.summary.lift_by_category) || [];
  const host = $("#lift-by-category");
  if (!host) return;
  const shown = rows.filter((r) => r.treated.n || r.control.n);
  if (!shown.length) {
    host.innerHTML = '<div class="empty">No control arm in this run.</div>';
    return;
  }
  host.innerHTML =
    '<table class="grid"><thead><tr><th>Cause</th><th class="num">Treated</th>' +
    '<th class="num">Control</th><th class="num">Lift</th><th>95% CI</th></tr></thead><tbody>' +
    shown.map(function (r) {
      const arms = '<td class="num">' + r.treated.recovered + "/" + r.treated.n + "</td>" +
                   '<td class="num">' + r.control.recovered + "/" + r.control.n + "</td>";
      if (r.lift === null) {
        return "<tr><th>" + esc(words(r.category)) + "</th>" + arms +
          '<td class="num dim">—</td><td class="dim">' + esc(r.reason || "") + "</td></tr>";
      }
      const ci = r.lift_ci95 || [0, 0];
      return "<tr><th>" + esc(words(r.category)) + "</th>" + arms +
        '<td class="num">' + (r.lift >= 0 ? "+" : "") + (100 * r.lift).toFixed(1) + "pp</td>" +
        "<td>[" + (100 * ci[0]).toFixed(1) + ", " + (100 * ci[1]).toFixed(1) + "]" +
        (r.significant ? "" : ' <span class="badge warn">spans zero</span>') + "</td></tr>";
    }).join("") + "</tbody></table>";
}

/* Brier, ECE and the per-pair gap. The direction column is the useful one: Brier
   punishes confident errors, the gap says which way they run. */
function renderCalibration() {
  const cal = (state.summary && state.summary.calibration) || {};
  const host = $("#calibration");
  if (!host) return;
  if (!cal.available) {
    host.innerHTML = '<div class="empty">' + esc(cal.reason || "Nothing to score yet.") + "</div>";
    $("#calibration-head").textContent = "";
    return;
  }
  $("#calibration-head").textContent =
    "Brier " + cal.brier_score.toFixed(4) + " · ECE " + cal.ece.toFixed(4) +
    " · " + cal.n_scored + " executions scored";
  host.innerHTML =
    '<table class="grid"><thead><tr><th>Cause / action</th><th class="num">n</th>' +
    '<th class="num">prior said</th><th class="num">realised</th><th class="num">gap</th>' +
    "<th>direction</th></tr></thead><tbody>" +
    cal.by_pair.map(function (p) {
      return "<tr><th>" + esc(words(p.category)) + " / " + esc(words(p.action)) + "</th>" +
        '<td class="num">' + p.n + "</td>" +
        '<td class="num">' + p.prior.toFixed(3) + "</td>" +
        '<td class="num">' + p.realised.toFixed(3) + "</td>" +
        '<td class="num">' + (p.gap >= 0 ? "+" : "") + p.gap.toFixed(3) + "</td>" +
        '<td><span class="badge ' + (p.direction === "optimistic" ? "warn" : "") + '">' +
          esc(p.direction) + "</span></td></tr>";
    }).join("") + "</tbody></table>" +
    '<p class="foot-note">' + esc(cal.attribution) + "</p>";
}

/* What it declined to do — promoted out of the stop-status breakdown, because it is
   the strongest thing this system has to say. */
function renderDeclined() {
  const d = (state.summary && state.summary.declined_to_contact) || {};
  const host = $("#declined");
  if (!host) return;
  if (!d.by_reason) { host.innerHTML = ""; return; }
  $("#declined-head").textContent =
    d.n_declined + " cases · " + d.n_control_arm + " more held out to measure the rest";
  const rows = d.by_reason.filter(function (r) { return r.n && r.status !== "stopped_holdout"; });
  host.innerHTML = rows.length
    ? '<table class="grid"><thead><tr><th class="num">Cases</th><th>Why the agent said no</th>' +
      "</tr></thead><tbody>" + rows.map(function (r) {
        return '<tr><td class="num">' + r.n + "</td><td>" + esc(r.why) + "</td></tr>";
      }).join("") + "</tbody></table>" +
      '<p class="foot-note">' + esc(d.note) + "</p>"
    : '<div class="empty">This batch produced no refusals.</div>';
}

/* The number the project actually stands behind: money that came back BECAUSE of the
   agent, minus everything the agent spent to get it. Rendered from /api/summary; the
   arithmetic lives in metrics.net_recovery() and is never restated here. */
function netHtml() {
  const net = (state.summary && state.summary.net) || {};
  const costs = (state.summary && state.summary.costs) || {};
  if (net.gross_recovered_paise === undefined) return "";
  const row = (k, v, sub, cls) =>
    '<div class="net-row ' + (cls || "") + '">' +
      '<span class="net-k">' + esc(k) + "</span>" +
      '<span class="net-v">' + esc(v) + "</span>" +
      '<span class="net-s">' + esc(sub) + "</span>" +
    "</div>";
  const lines = [
    row("Gross recovered", rupees(net.gross_recovered_paise), "every rupee that came back", ""),
    row("Outreach", "− " + rupees(costs.outreach_paise),
        (costs.by_action && costs.by_action.SEND_UPDATE_LINK
          ? costs.by_action.SEND_UPDATE_LINK.n : 0) + " links, " +
        (costs.by_action && costs.by_action.PROMISE_TO_PAY
          ? costs.by_action.PROMISE_TO_PAY.n : 0) + " promises · silent retries cost nothing", "cost"),
    row("Human queue", "− " + rupees(costs.handoff_paise),
        (costs.n_handoff_cases || 0) + " cases handed to a person", "cost"),
    row("Net recovered", rupees(net.net_recovered_paise),
        net.cost_per_100_recovered === null ? "nothing recovered yet"
          : "₹" + net.cost_per_100_recovered + " spent per ₹100 recovered", "total"),
  ];
  if (net.incremental_available) {
    const ci = net.net_incremental_paise_ci95 || [0, 0];
    lines.push(row("Net incremental", rupees(net.net_incremental_paise),
      "attributable to the agent, after costs · 95% CI [" +
      rupees(ci[0]) + ", " + rupees(ci[1]) + "]", "total accent"));
  }
  return '<div class="net-block"><h3>Net of what it cost</h3>' + lines.join("") +
    '<p class="foot-note">Gross recovery cannot go down by sending more messages, which ' +
    "is what makes it the wrong headline. The modelled annoyance cost prices each " +
    "decision but is never booked here — only rupees that actually moved are.</p></div>";
}

/* ---------------------------------------------------------------- causes */
function renderCategories() {
  const rows = state.categories || [];
  const tbody = $("#categories tbody");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No cases yet.</td></tr>';
    return;
  }
  const max = Math.max.apply(null, rows.map((r) => r.recovery_rate || 0).concat([0.0001]));
  tbody.innerHTML = rows.map((r) =>
    "<tr>" +
      '<td><a href="#/cases" data-filter-category="' + esc(r.category) + '">' + esc(words(r.category)) + "</a></td>" +
      '<td class="num">' + r.n_cases + "</td>" +
      '<td class="num">' + r.n_recovered + "</td>" +
      '<td class="num">' + pct(r.recovery_rate) + "</td>" +
      '<td class="num">' + rupees(r.at_risk_paise) + "</td>" +
      '<td class="num">' + rupees(r.recovered_paise) + "</td>" +
      '<td><div class="bar"><span style="width:' +
        (100 * (r.recovery_rate || 0) / max).toFixed(1) + '%"></span></div></td>' +
    "</tr>").join("");
}

/* -------------------------------------------------------------- pipeline */
function renderFlow(el, compact) {
  const m = state.mechanism;
  if (!m || !el) return;
  const f = m.funnel;
  const steps = [
    { k: "Detect", v: f.events, s: "webhook events stored, deduped on the Razorpay event id" },
    { k: "Cases", v: f.cases, s: "one recovery episode per subscription" },
    { k: "Diagnose", v: f.diagnoses, s: "rules first, model only on what they miss" },
    { k: "Gate", v: f.blocked, s: "decisions blocked by an invariant", gate: true },
    { k: "Decide", v: f.decisions, s: "one action from the policy table" },
    { k: "Execute", v: f.executions, s: f.contacts + " of them contacted a customer" },
    { k: "Recovered", v: f.recovered, s: "confirmed by a recovery signal, never by sending a link" },
  ];
  el.innerHTML = steps.map((s, i) =>
    (i ? '<div class="arrow">' + icon("arrow") + "</div>" : "") +
    '<div class="step ' + (s.gate ? "gate" : "") + '">' +
      '<div class="step-k">' + esc(s.k) + "</div>" +
      '<div class="step-v">' + esc(s.v) + "</div>" +
      (compact ? "" : '<div class="step-s">' + esc(s.s) + "</div>") +
    "</div>").join("");
}

function renderPipeline() {
  renderFlow($("#flow"), false);
  const m = state.mechanism;
  if (!m) return;
  const b = m.bounds;
  $("#funnel-notes").innerHTML =
    '<div class="panel-head"><h2>Bounds the loop runs under</h2></div>' +
    '<div class="panel-body"><dl class="kv">' +
      "<dt>Max attempts per case</dt><dd>" + b.max_attempts +
        " — attempt " + b.max_attempts + " is terminal in every policy row</dd>" +
      "<dt>Contact cooldown</dt><dd>" + b.cooldown_hours + " h between two messages to one customer</dd>" +
      "<dt>Episode window</dt><dd>" + b.episode_window_days +
        " days, after which an open case closes rather than lingering</dd>" +
      "<dt>Promise-to-pay window</dt><dd>" + b.promise_window_hours +
        " h grace, then handoff — never another retry</dd>" +
    "</dl></div>";
}

/* ------------------------------------------------------------ guardrails */
function renderGuards() {
  const m = state.mechanism;
  if (!m) return;
  $("#guards").innerHTML = m.invariants.map((i) =>
    '<div class="guard">' +
      '<div class="guard-head">' +
        '<span class="guard-code ' + esc(i.kind || "") + '">' + esc(i.code) + "</span>" +
        "<h4>" + esc(i.title) + "</h4>" +
        '<span class="guard-kind">' + esc(words(i.kind || "safety")) + "</span>" +
        '<span class="counts">' +
          '<span class="badge ' + (i.stops ? "danger" : "") + '">' + i.stops +
            " stop" + (i.stops === 1 ? "" : "s") + "</span>" +
          (i.defers ? '<span class="badge warn">' + i.defers + " deferred</span>" : "") +
        "</span>" +
      "</div>" +
      '<p class="guard-plain">' + esc(i.plain) + "</p>" +
      '<div class="guard-rule">' + esc(i.rule) + "</div>" +
    "</div>").join("");
  renderFences();
}

/* ---------------------------------------------------------------- fences */
const FENCE_PHASES = [
  ["pre_dispatch", "Before dispatch",
   "Re-fetch the subscription immediately before any contact leaves. Blocking stops the cycle; no attempt is spent."],
  ["post_dispatch", "After the write",
   "Re-fetch once a payment link exists. Too late not to create it; not too late to cancel it and say so."],
  ["inference", "Around the model call",
   "A SHA-256 over decision-relevant fields only, taken before the call and rechecked after. Notes, timestamps and customer metadata never move it."],
];

function renderFences() {
  const f = (state.summary || {}).fencing;
  const host = $("#fences");
  if (!host) return;
  if (!f) { host.innerHTML = '<p class="dim">No fencing data in this batch.</p>'; return; }

  $("#fence-claim").textContent = f.claim || "";
  host.innerHTML =
    '<table class="grid"><thead><tr>' +
      "<th>Fence</th><th class='num'>clear</th><th class='num'>settled</th>" +
      "<th class='num'>changed</th><th class='num'>unverified</th></tr></thead><tbody>" +
    FENCE_PHASES.map(function (row) {
      const v = (f.by_phase || {})[row[0]] || {};
      return "<tr><th><div>" + esc(row[1]) + "</div>" +
        '<div class="dim" style="font-weight:400">' + esc(row[2]) + "</div></th>" +
        '<td class="num">' + (v.clear || 0) + "</td>" +
        '<td class="num">' + (v.settled || 0) + "</td>" +
        '<td class="num">' + (v.changed || 0) + "</td>" +
        '<td class="num">' + (v.unverified || 0) + "</td></tr>";
    }).join("") + "</tbody></table>" +
    '<div class="legend" style="margin-top:12px">' +
      '<span class="key"><span class="badge ' + (f.outreach_to_settled ? "danger" : "ok") + '">' +
        f.outreach_to_settled + "</span>&nbsp;outreach to already-settled customers, of " +
        f.n_dispatches_fenced + " dispatches fenced</span>" +
      '<span class="key"><span class="badge">' + (f.stopped_already_settled || 0) +
        "</span>&nbsp;cases stopped as already settled</span>" +
      '<span class="key"><span class="badge">' + (f.n_compensations || 0) +
        "</span>&nbsp;compensation entries in the audit trail</span>" +
    "</div>" +
    ((f.outreach_to_settled_case_ids || []).length
      ? '<p class="dim mono" style="margin-top:8px">' +
          esc(f.outreach_to_settled_case_ids.join(" · ")) + "</p>"
      : "");
}

/* ---------------------------------------------------------------- policy */
function renderPolicy() {
  const m = state.mechanism;
  if (!m) return;
  const attempts = m.bounds.max_attempts;
  const cats = m.categories;
  const cell = (cat, att) => {
    const p = m.policy.find((x) => x.category === cat && x.attempt === att);
    if (!p) return "<td></td>";
    return '<td><div class="cell a-' + esc(p.action) + '">' +
      '<div class="act">' + esc(words(p.action)) + "</div>" +
      '<div class="delay">' + (p.delay_hours ? "+" + p.delay_hours + " h" : "immediate") + "</div>" +
      "</div></td>";
  };
  const head = "<thead><tr><th>Cause</th>" +
    Array.from({ length: attempts }, (_, i) => "<th>Attempt " + (i + 1) + "</th>").join("") + "</tr></thead>";
  const body = "<tbody>" + cats.map((c) =>
    "<tr><th>" + esc(words(c)) + "</th>" +
    Array.from({ length: attempts }, (_, i) => cell(c, i + 1)).join("") + "</tr>").join("") + "</tbody>";
  $("#policy").innerHTML = head + body;
  $("#policy-note").textContent = cats.length + " × " + attempts + " = " +
    (cats.length * attempts) + " cells, all filled";
  $("#policy-legend").innerHTML = m.actions.map((a) =>
    '<span class="key"><span class="sw" style="background:' +
      (a === "RETRY_LATER" ? "var(--accent)" : a === "SEND_UPDATE_LINK" ? "var(--ok)" :
       a === "PROMISE_TO_PAY" ? "var(--warn)" : "var(--neutral)") + '"></span>' +
      esc(words(a)) + "</span>").join("");

  $("#rules tbody").innerHTML = m.rules.map((r) =>
    "<tr>" +
      '<td class="mono">' + esc(r.id) + "</td>" +
      "<td>" + esc(words(r.category)) + "</td>" +
      '<td class="num">' + r.n_matched + "</td>" +
      '<td class="dim">' + (r.patterns.length
        ? esc(r.patterns.join(" · ")) + (r.n_patterns > r.patterns.length
          ? ' <span class="dim">+' + (r.n_patterns - r.patterns.length) + " more</span>" : "")
        : "<em>fallback — nothing matched</em>") + "</td>" +
    "</tr>").join("");
}

/* ----------------------------------------------------------------- model */
function renderModel() {
  const m = state.mechanism, s = state.summary;
  if (!m || !s) return;
  const llm = s.llm || {};
  const b = s.bounds || {};
  const modes = s.execution_modes || {};
  $("#boundary").innerHTML =
    '<div class="grid-2">' +
      '<div class="panel"><div class="panel-head"><h2>What the model may do</h2></div>' +
        '<div class="panel-body"><dl class="kv">' +
          "<dt>Classify a cause</dt><dd>only when no rule matched — " + (llm.classified || 0) +
            " case(s) this run, of which " + (llm.classified_to_unknown || 0) +
            " resolved to <code>unknown</code> and stopped</dd>" +
          "<dt>Draft message copy</dt><dd>" + (llm.drafted || 0) + " draft(s), of which " +
            (llm.fallback_to_template || 0) +
            " were rejected by the validator and replaced with a static template</dd>" +
        "</dl>" +
        '<p class="foot-note">Output is constrained to a fixed enum and a confidence floor of ' +
          esc(b.llm_confidence_threshold) +
          ". Anything below it is <code>unknown</code>, which stops the case.</p></div></div>" +
      '<div class="panel"><div class="panel-head"><h2>What it may never do</h2></div>' +
        '<div class="panel-body"><ul style="margin:0;padding-left:18px;line-height:1.85;' +
          'color:var(--text-2);font-size:12.5px">' +
          "<li>Choose an intervention — that is a static table lookup.</li>" +
          "<li>Reach the executor. No money-moving call is reachable from a model output.</li>" +
          "<li>Write a number. Drafts may contain no digit at all; amounts arrive by slot " +
            "substitution, so an invented figure is unrepresentable, not merely detected.</li>" +
          "<li>Write a URL. Links are injected by code after validation.</li>" +
          "<li>Override an invariant. The invariants module imports neither the policy table " +
            "nor the model.</li>" +
        "</ul></div></div>" +
    "</div>" +
    '<div class="panel"><div class="panel-head"><h2>Copy validator</h2><span class="spacer"></span>' +
      '<span class="muted">every rule below is checked deterministically before a message is used</span></div>' +
      '<div class="panel-body"><dl class="kv">' +
        '<dt>Allowed slots</dt><dd class="mono">' + esc(m.copy.slots.join("  ")) + "</dd>" +
        "<dt>Max rendered length</dt><dd>" + esc(m.copy.max_chars) + " characters</dd>" +
        '<dt>Forbidden words</dt><dd class="mono">' + esc(m.copy.forbidden.join("  ")) + "</dd>" +
        '<dt>Mandatory disclosure</dt><dd class="mono">' + esc(m.copy.disclosure) + "</dd>" +
      "</dl></div></div>" +
    '<div class="panel"><div class="panel-head"><h2>Execution modes</h2></div>' +
      '<div class="panel-body"><dl class="kv">' +
        "<dt>Real Razorpay test-mode calls</dt><dd>" + (modes.razorpay_test || 0) + "</dd>" +
        "<dt>Simulated</dt><dd>" + (modes.simulated || 0) +
          " — recorded as simulated, never dressed up as live</dd>" +
        "<dt>Provider</dt><dd>" + esc(b.llm_provider || "none") +
          (b.llm_model ? " · " + esc(b.llm_model) : "") + "</dd>" +
      "</dl></div></div>";
}

/* ----------------------------------------------------------------- cases */
const caseArm = (c) => (c.is_holdout ? "control" : "treated");

/* Sorting is client-side because the whole case list is already in memory — a round
   trip to re-order ~100 rows would be slower than the render. Numbers compare as
   numbers, everything else as text, and the sort is stable so a second key keeps the
   order of the first. */
function sortRows(rows) {
  const { key, dir } = state.sort;
  const sign = dir === "descending" ? -1 : 1;
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    if (x === null || x === undefined) x = "";
    if (y === null || y === undefined) y = "";
    if (typeof x === "number" && typeof y === "number") return sign * (x - y);
    return sign * String(x).localeCompare(String(y), undefined, { numeric: true });
  });
  return rows;
}

function applySortHeaders() {
  $$("#cases-table th.sortable").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.sort === state.sort.key ? state.sort.dir : "none");
  });
}

function renderCases() {
  const status = $("#f-status").value, cat = $("#f-category").value;
  const arm = $("#f-arm").value, text = $("#f-text").value.trim().toLowerCase();

  const rows = state.cases.filter((c) =>
    (!status || c.status === status) &&
    (!cat || c.category === cat) &&
    (!arm || caseArm(c) === arm) &&
    (!text || (c.case_id + " " + c.subscription_id).toLowerCase().indexOf(text) !== -1));

  sortRows(rows);

  applySortHeaders();
  $("#case-count").textContent = rows.length + " of " + state.cases.length + " cases";
  const tbody = $("#cases-table tbody");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">Nothing matches these filters.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((c) =>
    '<tr data-case="' + esc(c.case_id) + '" tabindex="0">' +
      '<td class="mono">' + esc(c.case_id.replace(/^case_/, "")) + "</td>" +
      '<td class="mono dim">' + esc(c.subscription_id) + "</td>" +
      "<td>" + (c.category ? esc(words(c.category)) : '<span class="dim">—</span>') + "</td>" +
      '<td class="num">' + rupees2(c.amount_rupees) + "</td>" +
      '<td class="num">' + c.attempt_count + "</td>" +
      "<td>" + statusBadge(c.status) + "</td>" +
      '<td><span class="badge ' + (c.is_holdout ? "control" : "") + '">' +
        (c.is_holdout ? "control" : "treated") + "</span></td>" +
      '<td><span class="badge ' + (c.synthetic ? "" : "accent") + '">' +
        (c.synthetic ? "synthetic" : "live") + "</span></td>" +
      '<td class="dim nowrap">' + when(c.updated_at) + "</td>" +
    "</tr>").join("");
}

function fillCaseFilters() {
  const statuses = Array.from(new Set(state.cases.map((c) => c.status))).sort();
  const cats = Array.from(new Set(state.cases.map((c) => c.category).filter(Boolean))).sort();
  const keep = (sel, values) => {
    const el = $(sel), current = el.value;
    el.innerHTML = '<option value="">all</option>' +
      values.map((v) => '<option value="' + esc(v) + '">' + esc(words(v)) + "</option>").join("");
    if (values.indexOf(current) !== -1) el.value = current;
  };
  keep("#f-status", statuses);
  keep("#f-category", cats);
}

/* ------------------------------------------------------------ case drawer */
function openDrawer() {
  // Remember the row that opened this, so closing returns the keyboard where it was
  // instead of dumping focus back at the top of the document.
  if (!$("#drawer").classList.contains("on")) {
    state.lastFocus = document.activeElement;
  }
  $("#drawer").classList.add("on");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#drawer-back").classList.add("on");
}

function closeCase() {
  const drawer = $("#drawer");
  if (!drawer.classList.contains("on")) return;
  drawer.classList.remove("on");
  drawer.setAttribute("aria-hidden", "true");
  $("#drawer-back").classList.remove("on");
  if (state.lastFocus && document.contains(state.lastFocus)) state.lastFocus.focus();
  state.lastFocus = null;
}

/* A modal that lets Tab wander behind the scrim is not modal. Cycle within the drawer
   while it is open; Escape still closes it (wired in boot). */
function trapFocus(ev) {
  if (ev.key !== "Tab") return;
  const drawer = $("#drawer");
  if (!drawer.classList.contains("on")) return;
  const items = $$('a[href], button:not([disabled]), input, select, summary, [tabindex]:not([tabindex="-1"])', drawer)
    .filter((el) => el.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
}

async function openCase(caseId) {
  $("#detail-id").textContent = caseId;
  $("#detail-body").innerHTML =
    '<div class="skeleton" style="height:14px;margin-bottom:8px"></div>' +
    '<div class="skeleton" style="height:14px;width:70%"></div>';
  openDrawer();
  $("#close-detail").focus();

  let d;
  try { d = await api("/api/cases/" + encodeURIComponent(caseId)); }
  catch (e) { $("#detail-body").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; return; }

  const c = d.case;

  const decisions = d.decisions.map((x) =>
    "<tr>" +
      '<td class="num">' + x.attempt_number + "</td>" +
      '<td><span class="badge ' + (ACTION_ROLE[x.action] || "") + '">' + esc(words(x.action)) + "</span></td>" +
      '<td class="mono dim">' + esc(x.policy_row_ref) + "</td>" +
      "<td>" + esc(words(x.status)) + "</td>" +
      '<td class="dim nowrap">' + when(x.scheduled_for) + "</td>" +
      "<td>" + (x.execution
        ? '<span class="badge ' + (x.execution.status === "success" ? "ok" : "danger") + '">' +
          esc(x.execution.mode) + "</span>"
        : '<span class="dim">—</span>') + "</td>" +
    "</tr>").join("");

  const copies = d.decisions.filter((x) => x.execution && x.execution.message_copy).map((x) =>
    '<div class="msg-copy">' +
      '<div class="dim" style="font-size:10.5px;margin-bottom:4px">attempt ' + x.attempt_number +
        " · " + esc(x.execution.copy_source || "") +
        " · " + esc(x.execution.simulated_channel || "") + " · never transmitted</div>" +
      esc(x.execution.message_copy) +
    "</div>").join("");

  $("#detail-body").innerHTML =
    '<div class="panel"><div class="panel-head"><h2>Case</h2><span class="spacer"></span>' +
      statusBadge(c.status) +
      '<span class="badge ' + (c.is_holdout ? "control" : "") + '">' +
        (c.is_holdout ? "control arm" : "treated") + "</span></div>" +
      '<div class="panel-body"><dl class="kv">' +
        '<dt>Subscription</dt><dd class="mono">' + esc(c.subscription_id) + "</dd>" +
        '<dt>Customer</dt><dd class="mono">' + esc(c.customer_id) + "</dd>" +
        "<dt>At risk</dt><dd>" + rupees2(c.amount_rupees) + "</dd>" +
        "<dt>Diagnosed cause</dt><dd>" + (c.current_category ? esc(words(c.current_category)) : "—") + "</dd>" +
        "<dt>Attempts used</dt><dd>" + c.attempt_count + " of " + d.bounds.max_attempts + "</dd>" +
        "<dt>Opted out</dt><dd>" + (c.customer_opted_out ? "yes — nothing may be sent" : "no") + "</dd>" +
        "<dt>Opened</dt><dd>" + when(c.created_at) + "</dd>" +
        "<dt>Closed</dt><dd>" + when(c.closed_at) + "</dd>" +
      "</dl>" +
      (c.status === "open" && !c.customer_opted_out
        ? '<button class="btn ghost" id="do-optout" style="margin-top:12px">' +
          "Mark customer opted out (demonstrates I3)</button>"
        : "") +
      "</div></div>" +

    (d.decisions.length
      ? '<div class="panel"><div class="panel-head"><h2>Decisions</h2></div>' +
        '<table class="grid"><thead><tr><th class="num">#</th><th>Action</th><th>Policy row</th>' +
        "<th>Status</th><th>Scheduled</th><th>Executed as</th></tr></thead>" +
        "<tbody>" + decisions + "</tbody></table></div>"
      : "") +

    (copies
      ? '<div class="panel"><div class="panel-head"><h2>Message copy, as composed</h2></div>' +
        '<div class="panel-body">' + copies + "</div></div>"
      : "") +

    '<div class="panel"><div class="panel-head"><h2>Audit trail</h2><span class="spacer"></span>' +
      '<span class="muted">' + d.audit_trail.length + " entries, append-only</span></div>" +
      '<div class="panel-body"><ol class="timeline">' +
        d.audit_trail.map((a) =>
          '<li class="s-' + esc(a.stage) + '">' +
            '<div class="t-head"><span class="badge">' + esc(a.stage) + "</span>" +
              '<span class="dim" style="font-size:10.5px">' + esc(a.actor) + "</span>" +
              '<span class="t-time">' + when(a.created_at) + "</span></div>" +
            '<div class="t-sum">' + esc(a.summary) + "</div>" +
            "<details><summary>detail</summary><pre>" +
              esc(JSON.stringify(a.detail, null, 2)) + "</pre></details>" +
          "</li>").join("") +
      "</ol></div></div>";

  const optout = $("#do-optout");
  if (optout) {
    optout.addEventListener("click", async () => {
      optout.disabled = true;
      try {
        await post("/api/cases/" + encodeURIComponent(caseId) + "/opt-out");
        toast("Customer opted out — the next gate stops this case", "ok");
        await refresh();
        openCase(caseId);
      } catch (e) { toast(e.message, "err"); optout.disabled = false; }
    });
  }
}

/* ----------------------------------------------------------- trace drawer */
async function showTrace(metric) {
  try {
    const t = await api("/api/metrics/trace/" + encodeURIComponent(metric));
    $("#detail-id").textContent = "trace · " + metric;
    $("#detail-body").innerHTML =
      '<div class="panel"><div class="panel-head"><h2>' + esc(words(metric)) + "</h2>" +
        '<span class="spacer"></span><span class="muted">' + t.n_cases + " case ids</span></div>" +
        '<div class="panel-body"><dl class="kv">' +
          "<dt>Reported value</dt><dd>" +
            (t.value_rupees !== null && t.value_rupees !== undefined ? rupees2(t.value_rupees) : esc(t.value)) +
          "</dd>" +
          '<dt>Endpoint</dt><dd class="mono">GET /api/metrics/trace/' + esc(metric) + "</dd>" +
        "</dl>" +
        '<p class="foot-note">Every id below drills down to its own audit trail — click one.</p>' +
        '<div class="test-grid" style="margin-top:10px">' +
          t.case_ids.map((id) =>
            '<div class="test-row" data-case="' + esc(id) + '" tabindex="0" style="cursor:pointer">' +
            '<span class="nm">' + esc(id) + "</span></div>").join("") +
        "</div></div></div>";
    openDrawer();
  } catch (e) { toast(e.message, "err"); }
}

/* ========================================================== control room */
function renderControlStatics() {
  const c = state.control;
  if (!c) return;
  const sel = $("#test-file");
  if (sel.options.length <= 1 && c.test_files) {
    sel.innerHTML = '<option value="">whole suite</option>' +
      c.test_files.map((f) => '<option value="tests/' + esc(f) + '">' + esc(f) + "</option>").join("");
  }
  const cat = $("#i-category");
  if (!cat.options.length && c.categories) {
    cat.innerHTML = c.categories.map((k) =>
      '<option value="' + esc(k) + '">' + esc(words(k)) + "</option>").join("");
  }
  const livedemoCat = $("#livedemo-category");
  if (livedemoCat && !livedemoCat.options.length && c.categories) {
    c.categories.forEach((k) => {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = words(k);
      livedemoCat.appendChild(opt);
    });
  }
  updateCommands();
  if (!c.enabled) {
    $$("#actions button").forEach((b) => { b.disabled = true; });
    toastOnce("control-off", "The control API is disabled (CONTROL_API_ENABLED=false).", "err");
  }
}

function updateCommands() {
  const n = $("#b-n").value, seed = $("#b-seed").value, h = $("#b-holdout").value;
  $("#cmd-batch").textContent =
    "python scripts/generate_synthetic.py --n " + n + " --seed " + seed +
    " && python scripts/run_batch.py --holdout " + h;
  $("#cmd-tests").textContent = "python -m pytest " + ($("#test-file").value || "tests") + " -v";
}

function consoleWrite(lines, reset) {
  const el = $("#console");
  if (reset) el.textContent = "";
  const frag = document.createDocumentFragment();
  lines.forEach((ln) => {
    const span = document.createElement("span");
    let cls = "l-" + ln.stream;
    if (/\bPASSED\b/.test(ln.text)) cls += " pass";
    if (/\bFAILED\b|\bERROR\b/.test(ln.text)) cls += " fail";
    span.className = cls;
    span.textContent = ln.text + "\n";   // textContent, never innerHTML: this is subprocess output
    frag.appendChild(span);
  });
  el.appendChild(frag);
  el.scrollTop = el.scrollHeight;
}

function setJobStatus(job) {
  const badge = $("#job-status");
  const map = { running: "accent", passed: "ok", failed: "danger", error: "danger" };
  badge.className = "badge " + (job ? (map[job.status] || "") : "");
  badge.textContent = job ? job.status : "idle";
  $("#job-label").textContent = job ? job.label : "";
  $("#job-elapsed").textContent = job ? job.elapsed_seconds.toFixed(1) + "s" : "";
  const busy = !!(job && job.status === "running");
  $$("#actions button").forEach((b) => { b.disabled = busy; });
}

async function startJob(path, payload) {
  try {
    const job = await post(path, payload);
    state.job = job;
    $("#job-result").innerHTML = "";
    consoleWrite(job.lines || [], true);
    state.jobCursor = job.next || 0;
    setJobStatus(job);
    pollJob();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function pollJob() {
  if (state.jobTimer) clearTimeout(state.jobTimer);
  if (!state.job) return;
  try {
    const s = await api("/api/control/jobs/" + state.job.id + "?after=" + state.jobCursor);
    if (s.lines.length) consoleWrite(s.lines, false);
    state.jobCursor = s.next;
    state.job = s;
    setJobStatus(s);
    if (s.status === "running") {
      state.jobTimer = setTimeout(pollJob, 700);
      return;
    }
    renderJobResult(s);
    if (s.kind === "batch") {
      await refresh();
      toast(s.status === "passed"
        ? "Batch replayed — every panel is now this run"
        : "Batch failed, see console", s.status === "passed" ? "ok" : "err");
    } else if (s.kind === "tests") {
      // pytest's own totals line, not a count of the lines this dashboard managed to
      // parse — a parser that quietly drops a test must not be able to under-report.
      const totals = (s.result && s.result.totals) || "";
      toast(s.status === "passed" ? (totals || "tests passed") : "Tests failed — " + (totals || "see console"),
        s.status === "passed" ? "ok" : "err");
    }
  } catch (e) {
    toast(e.message, "err");
    setJobStatus(null);
  }
}

function renderJobResult(job) {
  const el = $("#job-result");
  if (job.kind === "tests" && job.result && job.result.tests) {
    const r = job.result;
    el.innerHTML =
      '<div class="result-note ' + (job.status === "passed" ? "ok" : "fail") + '">' +
        icon(job.status === "passed" ? "check" : "warn") +
        "<span>" + esc(r.totals || (r.n + " tests")) + " — ran as <code>" +
          esc(job.command[0]) + "</code></span></div>" +
      '<div class="panel-body"><div class="test-grid">' +
        r.tests.map((t) =>
          '<div class="test-row ' + (t.outcome === "PASSED" ? "pass" : "fail") + '">' +
            '<span class="glyph">' + (t.outcome === "PASSED" ? "✓" : "✕") + "</span>" +
            '<span class="nm" title="' + esc(t.file + "::" + t.name) + '">' + esc(t.name) + "</span>" +
          "</div>").join("") +
      "</div></div>";
  } else if (job.kind === "batch" && job.result && job.result.summary) {
    const s = job.result.summary;
    const n = (s.n_synthetic || 0) + (s.n_live || 0);
    el.innerHTML =
      '<div class="result-note ' + (job.status === "passed" ? "ok" : "fail") + '">' +
        icon(job.status === "passed" ? "check" : "warn") +
        "<span>" + rupees2(s.total_recovered_rupees) + " recovered of " +
          rupees2(s.total_at_risk_rupees) + " at risk across " + n + " cases.</span></div>";
  } else {
    el.innerHTML = "";
  }
}

function renderStormResult(r) {
  const ok = r.held;
  const rows = ["cases", "events", "decisions"].map((k) =>
    "<tr><td>" + esc(k) + " for this subscription</td>" +
      '<td class="num">' + r.observed[k] + '</td><td class="num">' + r.expected[k] + "</td>" +
      "<td>" + (r.observed[k] === r.expected[k]
        ? '<span class="badge ok">match</span>' : '<span class="badge danger">mismatch</span>') +
    "</td></tr>").join("");

  $("#job-result").innerHTML =
    '<div class="result-note ' + (ok ? "ok" : "fail") + '">' + icon(ok ? "check" : "warn") +
      "<span><strong>" + (ok ? "Held." : "Did not hold.") + "</strong> " + esc(r.explains) + "</span></div>" +
    '<div class="panel-body"><table class="grid"><thead><tr><th>Check</th>' +
      '<th class="num">Observed</th><th class="num">Expected</th><th>Verdict</th></tr></thead><tbody>' +
      rows +
      "<tr><td>exceptions escaping intake()</td>" +
        '<td class="num">' + r.errors.length + '</td><td class="num">0</td><td>' +
        (r.errors.length ? '<span class="badge danger">raised</span>' : '<span class="badge ok">none</span>') +
      "</td></tr>" +
    "</tbody></table>" +
    '<p class="foot-note">Responses: ' +
      Object.keys(r.responses).map((k) => r.responses[k] + "× <code>" + esc(k) + "</code>").join(" · ") +
      ". " + (r.case_id
        ? 'Opened <a href="#" data-case="' + esc(r.case_id) + '">' + esc(r.case_id) + "</a>."
        : "") +
    "</p></div>";
}

/* =============================================================== loading */
function skeletonRow(w) {
  return '<div class="skeleton" style="height:11px;width:' + w + '%;margin:9px 0"></div>';
}

/* The first paint has no data yet. An empty panel reads as "nothing happened"; a
   skeleton reads as "not yet", which is the true statement. */
function showSkeletons() {
  if (state.summary) return;                 // only before the first successful load
  $("#tiles").innerHTML = Array.from({ length: 6 }, () =>
    '<div class="tile">' + skeletonRow(55) + skeletonRow(80) + skeletonRow(40) + "</div>").join("");
  $("#cases-table tbody").innerHTML =
    '<tr><td colspan="9">' + [92, 78, 85, 70].map(skeletonRow).join("") + "</td></tr>";
}

async function refresh() {
  if (state.loading) return;
  state.loading = true;
  showSkeletons();
  const svg = $("#refresh").querySelector("svg");
  svg.classList.add("spin");
  $("#refresh").setAttribute("aria-busy", "true");
  try {
    const results = await Promise.all([
      api("/api/health"), api("/api/summary"), api("/api/mechanism"),
      api("/api/categories"), api("/api/cases"),
    ]);
    state.health = results[0]; state.summary = results[1]; state.mechanism = results[2];
    state.categories = results[3]; state.cases = results[4];
    try { state.control = await api("/api/control/state"); }
    catch (_) {
      state.control = { enabled: false, test_files: [], categories: state.mechanism.categories };
    }

    renderChips(); renderBanner(); renderOverview(); renderImpact(); renderCategories();
    renderPipeline(); renderGuards(); renderPolicy(); renderModel();
    fillCaseFilters(); renderCases(); renderControlStatics();
  } catch (e) {
    toast("Could not reach the API: " + e.message, "err");
  } finally {
    svg.classList.remove("spin");
    $("#refresh").removeAttribute("aria-busy");
    state.loading = false;
  }
}

/* ================================================================ router */
/* "#/mechanism:policy" -> view "mechanism", tab "policy". An unknown view or tab
   falls back to the first valid one rather than rendering a blank shell. */
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split(":");
  const name = VIEWS[parts[0]] ? parts[0] : "overview";
  const subs = VIEWS[name].subs;
  const sub = subs ? (subs[parts[1]] ? parts[1] : firstSub(name)) : null;
  const leaf = sub ? subs[sub] : VIEWS[name];

  $$(".view").forEach((v) => v.classList.toggle("on", v.dataset.view === name));
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === name));

  const bar = $('.subnav[data-sub-for="' + name + '"]');
  if (bar) {
    $$("a", bar).forEach((a) => a.classList.toggle("on", a.dataset.sub === sub));
    $$('.view[data-view="' + name + '"] > .subview').forEach((v) =>
      v.classList.toggle("on", v.dataset.sub === sub));
  }

  // A case file left hanging over a view it does not belong to reads as part of that
  // view. Navigating away closes it.
  closeCase();

  $("#view-title").textContent = leaf.title;
  $("#view-q").textContent = leaf.q;
  document.title = leaf.title + " — Sahara";
  // Screen readers otherwise stay parked wherever the old view was; only move focus on
  // a real navigation, never on the initial render.
  if (state.summary) $("#scroll").focus({ preventScroll: true });
}

/* ================================================================== wire */
function boot() {
  // One theme, so the stale preference from the old two-theme toggle is dropped
  // rather than left to set data-theme on a document that no longer reads it.
  localStorage.removeItem("ra-theme");
  document.documentElement.removeAttribute("data-theme");

  window.addEventListener("hashchange", route);
  route();

  $("#refresh").addEventListener("click", () => refresh());

  $("#tick").addEventListener("click", async () => {
    const b = $("#tick");
    b.disabled = true;
    try {
      const r = await post("/api/control/tick");
      toast("Tick: " + r.n_executed + " executed, " + r.promises_lapsed +
        " promise(s) lapsed, " + r.episodes_expired + " episode(s) closed", "ok");
      await refresh();
    } catch (e) { toast(e.message, "err"); }
    finally { b.disabled = false; }
  });

  const livedemoPay = $("#livedemo-pay");
  if (livedemoPay) {
    livedemoPay.addEventListener("click", async () => {
      const statusEl = $("#livedemo-status");
      livedemoPay.disabled = true;
      statusEl.textContent = "Creating order...";
      try {
        const data = await post("/api/demo/order");
        const pipelineUrl = "/live-pipeline.html?demo_id=" + encodeURIComponent(data.demo_id);
        // Razorpay's checkout always overlays whatever tab called rzp.open(), so the
        // pipeline has to live in a tab of its own to stay visible while it's open.
        window.open(pipelineUrl, "sahara-pipeline");
        statusEl.textContent = "Order " + data.order_id + " created. Pipeline tab opened — " +
          "switch to it, then fail the checkout that's about to appear here.";

        const rzp = new Razorpay({
          key: data.key_id,
          amount: data.amount,
          currency: data.currency,
          order_id: data.order_id,
          name: "Sahara — Test mode",
          description: "Recovery loop demo order",
          notes: { case_source: "live-demo", subscription_id: data.demo_id, customer_id: data.demo_id },
          handler: function (response) {
            statusEl.textContent = "Payment succeeded: " + response.razorpay_payment_id +
              " — nothing for the pipeline to react to. Try again and fail it instead.";
            livedemoPay.disabled = false;
          },
          modal: { ondismiss: function () { livedemoPay.disabled = false; } },
        });
        rzp.on("payment.failed", function (response) {
          statusEl.textContent = "Payment failed: " + response.error.description +
            " — check the pipeline tab.";
          livedemoPay.disabled = false;
        });
        rzp.open();
      } catch (e) {
        statusEl.textContent = "Error: " + e.message;
        livedemoPay.disabled = false;
      }
    });
  }

  const livedemoInject = $("#run-livedemo-inject");
  if (livedemoInject) {
    livedemoInject.addEventListener("click", async () => {
      const statusEl = $("#livedemo-inject-status");
      livedemoInject.disabled = true;
      statusEl.textContent = "Injecting...";
      try {
        const r = await post("/api/control/inject", {
          category: $("#livedemo-category").value,
          amount_rupees: Number($("#livedemo-amount").value),
        });
        if (r.case_id) {
          window.open("/live-pipeline.html?case_id=" + encodeURIComponent(r.case_id), "sahara-pipeline");
          statusEl.textContent = "Case " + r.case_id + " opened (" + r.requested_category +
            ") — pipeline tab opened.";
        } else {
          statusEl.textContent = "Event was " + r.status + " — no case opened.";
        }
        await refresh();
      } catch (e) {
        statusEl.textContent = "Error: " + e.message;
      } finally {
        livedemoInject.disabled = false;
      }
    });
  }

  document.addEventListener("click", (ev) => {
    const trace = ev.target.closest("[data-trace]");
    if (trace) { ev.preventDefault(); showTrace(trace.dataset.trace); return; }

    const filter = ev.target.closest("[data-filter-category]");
    if (filter) { $("#f-category").value = filter.dataset.filterCategory; renderCases(); return; }

    const row = ev.target.closest("[data-case]");
    if (row) { ev.preventDefault(); openCase(row.dataset.case); }
  });

  document.addEventListener("keydown", (ev) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (ev.key === "Enter" && ev.target.closest && ev.target.closest("[data-case]")) {
      ev.preventDefault();
      openCase(ev.target.closest("[data-case]").dataset.case);
    }
    if (ev.key === "Escape") closeCase();
    if (ev.key === "r" && !typing) refresh();
    if (ev.key === "/" && !typing) {
      ev.preventDefault();
      location.hash = "#/cases";
      $("#f-text").focus();
    }
  });

  $("#close-detail").addEventListener("click", closeCase);
  $("#drawer-back").addEventListener("click", closeCase);

  ["#f-status", "#f-category", "#f-arm"].forEach((s) => $(s).addEventListener("change", renderCases));
  $("#f-text").addEventListener("input", renderCases);

  $$("#cases-table th.sortable").forEach((th) => {
    const sortBy = () => {
      const key = th.dataset.sort;
      state.sort = {
        key: key,
        dir: state.sort.key === key && state.sort.dir === "ascending" ? "descending" : "ascending",
      };
      renderCases();
    };
    th.tabIndex = 0;
    th.addEventListener("click", sortBy);
    th.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); sortBy(); }
    });
  });

  document.addEventListener("keydown", trapFocus);

  ["#b-n", "#b-seed", "#b-holdout", "#test-file"].forEach((s) =>
    $(s).addEventListener("change", updateCommands));

  $("#run-tests").addEventListener("click", () =>
    startJob("/api/control/tests", { path: $("#test-file").value || null }));

  $("#run-batch").addEventListener("click", () => {
    const n = Number($("#b-n").value);
    const seed = Number($("#b-seed").value);
    const holdout = Number($("#b-holdout").value);
    if (!confirm("Replay " + n + " cases (seed " + seed + ", holdout " + holdout +
        ") into a fresh database?\n\nThis deletes the current recovery.db.")) return;
    startJob("/api/control/batch", { n: n, seed: seed, holdout: holdout, live_links: 0 });
  });

  $("#run-storm").addEventListener("click", async () => {
    const btn = $("#run-storm");
    btn.disabled = true;
    try {
      const r = await post("/api/control/storm", {
        n: Number($("#s-n").value), distinct: $("#s-mode").value === "distinct",
      });
      consoleWrite([
        { stream: "cmd", text: "$ POST /api/control/storm  n=" + r.n_fired + " distinct=" + r.distinct },
        { stream: "out", text: JSON.stringify(r, null, 2) },
      ], true);
      renderStormResult(r);
      toast(r.held ? "Storm held — one case, one live decision" : "Storm did NOT hold",
        r.held ? "ok" : "err");
      await refresh();
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  });

  $("#run-inject").addEventListener("click", async () => {
    const btn = $("#run-inject");
    btn.disabled = true;
    try {
      const r = await post("/api/control/inject", {
        category: $("#i-category").value, amount_rupees: Number($("#i-amount").value),
      });
      consoleWrite([
        { stream: "cmd", text: "$ POST /api/control/inject  " + $("#i-category").value },
        { stream: "out", text: JSON.stringify(r, null, 2) },
      ], true);
      $("#job-result").innerHTML = "";
      await refresh();
      if (r.case_id) { toast("Case opened — the file is on the right", "ok"); openCase(r.case_id); }
      else toast("Event was " + r.status, "info");
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  });

  $("#clear-console").addEventListener("click", () => {
    $("#console").textContent = "";
    $("#job-result").innerHTML = "";
  });

  refresh();
}

document.addEventListener("DOMContentLoaded", boot);
