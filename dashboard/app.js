/* Dashboard client (docs/06). Vanilla fetch, no framework, no build step.
   A Refresh button re-fetches; there is deliberately no polling and no websocket.

   The guardrail, policy and rule sections render whatever /api/mechanism returns.
   Nothing about the agent's rules is written down twice.

   ESCAPING IS STRUCTURAL. Markup is built with the html`` tag below, which escapes
   every interpolated value unless it is explicitly a Safe fragment. Escaping is
   therefore the default and safety does not depend on remembering to call esc() at
   each of a hundred call sites. Data here reaches the page from Razorpay webhook
   bodies stored verbatim in the database (error strings, ids), so it is treated as
   untrusted throughout. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const state = { cases: [], summary: null, mechanism: null };

/* ------------------------------------------------------------- escaping --- */
class Safe {                       // an already-escaped fragment
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
const raw = (s) => new Safe(s);    // use ONLY on markup this file authored
const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(v) {
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map(esc).join("");
  if (v === null || v === undefined || v === false) return "";
  return String(v).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}
const html = (strings, ...vals) =>
  raw(strings.reduce((out, s, i) => out + s + (i < vals.length ? esc(vals[i]) : ""), ""));
// esc(), not String(): a bare Array.toString() joins with commas, which would
// sprinkle stray "," between every tile, chip and table row.
const setHTML = (sel, v) => { $(sel).innerHTML = esc(v); };

/* ------------------------------------------------------------ formatting --- */
const fmtRupees = (paise) => "₹" + Math.round(paise / 100).toLocaleString("en-IN");
const pct = (x, d = 1) => (x * 100).toFixed(d) + "%";
const pp = (x, d = 1) => (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + " pp";
const shortTime = (iso) => (iso ? iso.replace("T", " ").replace("Z", "") : "—");
const titleCase = (s) => String(s ?? "").replace(/_/g, " ");
const width = (frac) => raw("width:" + Math.max(0, Math.min(1, frac || 0)) * 100 + "%");

const statusClass = (s) =>
  s === "recovered" ? "recovered" : s === "open" ? "open"
    : s === "stopped_holdout" ? "holdout" : "stopped";

async function get(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(path + " -> " + res.status);
  return res.json();
}

/* =============================================================== routing === */
const VIEWS = {
  overview:   ["Overview", "What is at stake, and what came back."],
  impact:     ["Impact", "Money recovered because of the agent — not merely alongside it."],
  causes:     ["By failure cause", "Where recovery is easy, and where it is not."],
  pipeline:   ["Pipeline", "What actually happened to the events that arrived."],
  guardrails: ["Guardrails", "The part of the system that says no."],
  policy:     ["Decision policy", "A lookup, not a judgement call."],
  model:      ["Model boundary", "What the model decides, and what it never touches."],
  cases:      ["Cases", "Every row opens the full case file."],
};

function route() {
  const name = (location.hash.replace(/^#\//, "") || "overview");
  const view = VIEWS[name] ? name : "overview";
  $$(".view").forEach((v) => v.classList.toggle("active", v.dataset.view === view));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $("#view-title").textContent = VIEWS[view][0];
  $("#view-q").textContent = VIEWS[view][1];
  const sc = $("#scroll"); if (sc) sc.scrollTop = 0;
}

/* ====================================================== rail + banner === */
function renderChips(s) {
  const b = s.bounds;
  const chips = [
    ["", `max ${b.max_attempts} attempts`],
    ["", `${b.cooldown_hours}h cooldown`],
    ["", `${b.episode_window_days}d window`],
    [b.llm_model ? "on" : "off", b.llm_model ? `model ${b.llm_model}` : "no model"],
  ];
  setHTML("#chips", chips.map(([cls, t]) => html`<span class="chip ${cls}">${t}</span>`));
  setHTML("#rail-note", html`Bounds are hardcoded, not configurable at runtime.
    Seed ${s.seed ?? "—"} · ${s.n_synthetic} synthetic · ${s.n_live} live test-mode.`);

  const inc = s.incremental || {};
  $("#nav-impact").textContent = inc.available ? pp(inc.lift, 0) : "—";
  $("#nav-cases").textContent = s.n_cases;
}

function renderBanner(s) {
  setHTML("#banner", html`<span>⚠</span><div><b>Synthetic demo data.</b>
    ${s.n_synthetic} of ${s.n_cases} cases are generated (seed ${s.seed ?? "—"});
    ${s.n_live} are Razorpay test-mode. No real customers.
    <b>No message was ever transmitted</b> — every notification is composed, validated and logged only.
    Time-to-recovery uses the ${s.time_basis}. Outcome probabilities are modelling assumptions,
    not measured market data.</div>`);
}

/* ============================================================== overview === */
function renderTiles(s) {
  const rate = s.recovery_rate;
  const ttr = s.avg_time_to_recovery_hours;
  const inc = s.incremental || {};

  const incTile = inc.available
    ? ["accent", "₹ incremental", fmtRupees(inc.incremental_paise_total),
       `${pp(inc.lift)} vs control · ${inc.significant ? "CI excludes zero" : "CI includes zero"}`]
    : ["", "₹ incremental", "—", "no control arm — run with --holdout"];

  const tiles = [
    ["", "₹ at risk", fmtRupees(s.total_at_risk_paise), `${s.n_cases} cases`],
    ["recovered", "₹ recovered (gross)", fmtRupees(s.total_recovered_paise),
     `${pct(rate.rate)} of ${rate.denominator} closed cases`],
    incTile,
    ["", "Avg time to recovery", ttr === null ? "—" : (ttr / 24).toFixed(1) + " d",
     ttr === null ? "no recoveries yet" : `${ttr} h (${s.time_basis})`],
    /* Stopped sits beside ₹ recovered at equal visual weight, on purpose (docs/07 §4). */
    ["stopped", "Stopped & handed off", String(s.stopped.total), "each with a complete case file"],
    ["llm", "Model involvement", `${s.llm.classified} + ${s.llm.drafted}`,
     `classified · copy drafts (${s.llm.fallback_to_template} template fallbacks)`],
  ];
  setHTML("#tiles", tiles.map(([cls, label, value, sub]) => html`
    <div class="tile ${cls}">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
      <div class="sub">${sub}</div>
    </div>`));
}

/* ------------------------------------------------ overview: outcome split -- */
function renderOutcomeSplit(s) {
  const by = s.stopped.by_status || {};
  const holdout = by.stopped_holdout || 0;
  const stoppedOther = s.stopped.total - holdout;
  const parts = [
    ["s-recovered", "Recovered", s.n_recovered, "money confirmed back"],
    ["s-stopped", "Stopped & handed off", stoppedOther, "each with a complete case file"],
    ["s-holdout", "Control arm", holdout, "deliberately never touched"],
    ["s-open", "Still open", s.n_open, "inside the episode window"],
  ].filter(([, , n]) => n > 0);
  const total = parts.reduce((a, [, , n]) => a + n, 0) || 1;

  setHTML("#outcome-split", html`
    <div class="split">${parts.map(([cls, , n]) =>
      html`<span class="${cls}" style="${width(n / total)}"></span>`)}</div>
    <div class="split-key">${parts.map(([cls, label, n, note]) => html`
      <div><i class="${cls}" style="background:${
        cls === "s-recovered" ? "#0a7146" : cls === "s-stopped" ? "#d9a03c"
        : cls === "s-holdout" ? "#475569" : "#e3e6eb"}"></i>
        <span>${label} <span class="muted">— ${note}</span></span>
        <span class="k-n">${n}</span></div>`)}</div>`);
}

function renderEffectMini(s) {
  const inc = s.incremental || {};
  if (!inc.available) {
    setHTML("#effect-mini", html`<div class="empty">
      No control arm in this batch, so every rupee shown is <b>gross</b> — it cannot be separated from
      what would have come back anyway. Re-run with <code>--holdout 0.3</code> to measure the difference.
    </div>`);
    return;
  }
  const [lo, hi] = inc.lift_ci95;
  setHTML("#effect-mini", html`
    <div class="lift-box ${inc.significant ? "" : "null"}" style="margin-top:0">
      <div class="lift-big">${pp(inc.lift)}</div>
      <div class="lift-sub">recovery-rate lift over the untreated control arm,
        95% CI [${pp(lo)}, ${pp(hi)}]</div>
      <div class="lift-sub" style="margin-top:7px">
        <b>${fmtRupees(inc.incremental_paise_total)}</b> incremental of
        <b>${fmtRupees(inc.gross_recovered_paise)}</b> gross
      </div>
    </div>`);
}

/* ================================================== did it work (effect) === */
function renderEffect(s) {
  const inc = s.incremental || {};
  if (!inc.available) {
    setHTML("#effect-panel", html`
      <div class="empty">
        <b>No control arm in this batch.</b> Every recovered rupee here is <em>gross</em> — it cannot be
        separated from what would have come back anyway.
        <pre>python scripts/run_batch.py --cases synthetic_cases.json --db recovery.db --holdout 0.3</pre>
        re-runs the batch with 30% of cases held out untreated, and this panel then reports the difference
        between the arms with a confidence interval.
      </div>`);
    return;
  }
  const t = inc.treated, c = inc.control;
  const max = Math.max(t.rate, c.rate, 0.01);
  const [lo, hi] = inc.lift_ci95;
  const [mlo, mhi] = inc.incremental_paise_ci95;

  setHTML("#effect-panel", html`
    <div class="arm-row">
      <div class="arm-label">Treated<small>agent ran</small></div>
      <div class="arm-bar treated"><span style="${width(t.rate / max)}"></span></div>
      <div class="arm-val">${pct(t.rate)} <small>${t.recovered}/${t.n}</small></div>
    </div>
    <div class="arm-row">
      <div class="arm-label">Control<small>held out</small></div>
      <div class="arm-bar control"><span style="${width(c.rate / max)}"></span></div>
      <div class="arm-val">${pct(c.rate)} <small>${c.recovered}/${c.n}</small></div>
    </div>

    <div class="lift-box ${inc.significant ? "" : "null"}">
      <div class="lift-big">${pp(inc.lift)}</div>
      <div class="lift-sub">
        95% CI [${pp(lo)}, ${pp(hi)}] — the interval
        <b>${inc.significant ? "excludes" : "includes"} zero</b>, so the direction of the effect
        ${inc.significant ? "is not in doubt" : "is not established"} at this sample size.
      </div>
      <div class="lift-sub" style="margin-top:8px">
        <b>${fmtRupees(inc.incremental_paise_total)}</b> incremental
        (95% CI [${fmtRupees(mlo)}, ${fmtRupees(mhi)}]) out of
        <b>${fmtRupees(inc.gross_recovered_paise)}</b> gross across ${t.n} treated cases.
      </div>
    </div>

    <p class="sec-note" style="margin:12px 0 0">
      <b>Intention-to-treat:</b> every case counts in the arm it was assigned to, whatever status it
      reached — a treated case the agent <em>refused</em> to act on stays in the treated denominator.
      Dropping those would flatter the result by exactly the cases handled most conservatively.
      The interval is sampling uncertainty only: it says how much of the gap could be chance at
      ${t.n + c.n} cases, not whether the underlying outcome model is right.
      Bootstrap over ${inc.bootstrap_samples.toLocaleString("en-IN")} resamples.
    </p>`);
}

/* ============================================================== the loop === */
function renderFlow(m) {
  const f = m.funnel;
  const stages = [
    [f.events, "Detect", "webhooks verified by signature and deduplicated", false],
    [f.diagnoses, "Diagnose", "into one of six fixed causes — rules first", false],
    [null, "Gate", "I1–I4 checked before any decision is made", true],
    [f.decisions, "Decide", "one action, from a static policy cell", false],
    [null, "Gate", "I1–I4 re-checked against fresh state", true],
    [f.executions, "Execute", `${f.contacts} of these put a message in front of a person`, false],
    [f.recovered, "Recovered", "confirmed by a real recovery signal, never by sending", false],
  ];
  setHTML("#flow", stages.map(([n, name, what, gate]) => html`
    <div class="stage-card ${gate ? "gate" : ""}">
      <div class="st-name">${name}</div>
      <div class="st-n">${gate ? (f.blocked ? f.blocked + " blocked" : "✓") : n.toLocaleString("en-IN")}</div>
      <div class="st-what">${what}</div>
    </div>`));
}

/* =========================================================== guardrails === */
function renderGuards(m) {
  const fired = m.invariants.reduce((n, i) => n + i.stops + i.defers, 0);
  $("#nav-guards").textContent = fired ? String(fired) : "";
  setHTML("#guards", m.invariants.map((i) => {
    const bits = [];
    if (i.stops) bits.push(html`stopped <b>${i.stops}</b> case${i.stops === 1 ? "" : "s"}`);
    if (i.defers) bits.push(html`delayed <b>${i.defers}</b> intervention${i.defers === 1 ? "" : "s"}`);
    const count = bits.length
      ? raw(bits.map(String).join(" · "))
      : html`<span class="muted">never needed to fire in this batch — the policy table stayed inside it</span>`;
    return html`
      <div class="guard">
        <div class="code">${i.code}</div>
        <div>
          <div class="g-title">${i.title}</div>
          <div class="g-plain">${i.plain}</div>
          <div class="g-count">${count}</div>
          <div class="g-rule">enforced as: ${i.rule}</div>
        </div>
      </div>`;
  }));
}

/* ======================================================== policy + rules === */
function renderPolicy(m) {
  const atts = [...new Set(m.policy.map((p) => p.attempt))].sort();
  const head = html`<thead><tr><th></th>${
    atts.map((a) => html`<th>Attempt ${a}</th>`)}</tr></thead>`;
  const body = m.categories.map((cat) => {
    const cells = atts.map((a) => {
      const cell = m.policy.find((p) => p.category === cat && p.attempt === a);
      if (!cell) return html`<td></td>`;
      const note = cell.action === "STOP_HANDOFF" ? "to a human"
        : cell.delay_hours ? `after ${cell.delay_hours}h` : "immediately";
      return html`<td><div class="cell ${cell.action}">
        <div class="act">${titleCase(cell.action)}</div>
        <div class="dly">${note}</div>
      </div></td>`;
    });
    return html`<tr><td class="rowhead">${cat}</td>${cells}</tr>`;
  });
  setHTML("#policy", html`${head}<tbody>${body}</tbody>`);

  $("#policy-note").textContent =
    `${m.policy.length} cells · every one filled, so the lookup cannot fall through`;

  setHTML("#policy-legend", m.actions.map((a) =>
    html`<span><i class="cell ${a}" style="border-radius:3px"></i>${titleCase(a)}</span>`));

  const maxHits = Math.max(1, ...m.rules.map((r) => r.n_matched));
  setHTML("#rules tbody", m.rules.map((r) => html`
    <tr>
      <td class="mono">${r.id}</td>
      <td>${r.category}</td>
      <td class="num">${r.n_matched}</td>
      <td>
        <div class="bar" style="${width(r.n_matched / maxHits)};margin-bottom:4px">
          <span style="width:100%"></span></div>
        <span class="muted mono">${r.patterns.length
          ? r.patterns.join(" · ") + (r.n_patterns > r.patterns.length
              ? ` … +${r.n_patterns - r.patterns.length} more` : "")
          : "no text to classify — never sent to a model"}</span>
      </td>
    </tr>`));
}

/* ============================================================== boundary === */
function renderBoundary(s, m) {
  const rows = [
    ["Failure classification, clear cases", "Rule table R1–R7 on Razorpay error fields", false],
    ["Failure classification, ambiguous cases",
     "Output forced into the six-value enum; below the confidence threshold ⇒ unknown ⇒ stop", true],
    ["Which intervention to run", "Static policy table", false],
    ["Retry timing", "Hardcoded delays per policy row", false],
    ["Stopping rules I1–I4", "Independent module, run before every decision and every execution", false],
    ["Message wording",
     "Drafts a slot skeleton; a deterministic validator checks it; static template on rejection", true],
    ["Money-moving execution", "Deterministic executor calling Razorpay test-mode APIs", false],
  ];
  setHTML("#boundary", html`
    <table class="grid">
      <thead><tr><th>Capability</th><th>Owner</th></tr></thead>
      <tbody>${rows.map(([cap, owner, isLlm]) => html`
        <tr>
          <td>${cap}</td>
          <td>${isLlm ? html`<span class="pill llm">model</span> ` : ""}${owner}</td>
        </tr>`)}
      </tbody>
    </table>
    <p class="sec-note" style="margin:14px 0 0">
      <b>The model cannot type a digit.</b> Copy is drafted as a slot skeleton —
      ${m.copy.slots.map((x) => html`<code>${x}</code> `)}— and every value is substituted by code
      afterwards. An invented amount is not merely detected, it is unrepresentable. Drafts are capped at
      ${m.copy.max_chars} characters, must carry the literal <code>${m.copy.disclosure}</code> disclosure,
      and may never contain a URL. In this batch the model produced <b>${s.llm.drafted}</b> accepted
      drafts against <b>${s.llm.fallback_to_template}</b> static templates.
    </p>`);
}

/* ============================================================ categories === */
function renderCategories(rows) {
  const max = Math.max(0.0001, ...rows.map((r) => r.recovery_rate));
  setHTML("#categories tbody", rows.map((r) => html`
    <tr>
      <td class="mono">${r.category}</td>
      <td class="num">${r.n_cases}</td>
      <td class="num">${r.n_recovered}</td>
      <td class="num">${pct(r.recovery_rate)}</td>
      <td class="num">${fmtRupees(r.recovered_paise)}</td>
      <td class="num">${r.n_stopped}</td>
      <td class="bar-col"><div class="bar"><span style="${width(r.recovery_rate / max)}"></span></div></td>
    </tr>`));
}

/* ============================================================== case list === */
function attemptDots(used, max) {
  const dots = [];
  for (let i = 0; i < max; i++) dots.push(html`<i class="${i < used ? "used" : ""}"></i>`);
  return html`<span class="dots" title="${used} of ${max} attempts used">${dots}</span>`;
}

function renderCases() {
  const st = $("#f-status").value, cat = $("#f-category").value;
  const arm = $("#f-arm").value, q = $("#f-text").value.trim().toLowerCase();
  const max = state.summary.bounds.max_attempts;
  const rows = state.cases.filter((c) =>
    (!st || c.status === st) &&
    (!cat || c.category === cat) &&
    (!arm || (arm === "control") === !!c.is_holdout) &&
    (!q || c.case_id.toLowerCase().includes(q) ||
      (c.subscription_id || "").toLowerCase().includes(q)));

  $("#case-count").textContent = `${rows.length} of ${state.cases.length}`;
  setHTML("#cases-table tbody", rows.length
    ? rows.map((c) => html`
      <tr data-id="${c.case_id}">
        <td class="mono">${c.case_id}</td>
        <td class="mono">${c.subscription_id}</td>
        <td>${c.category ?? "—"}</td>
        <td class="num">${fmtRupees(c.amount_at_risk_paise)}</td>
        <td>${attemptDots(c.attempt_count, max)}</td>
        <td><span class="pill ${statusClass(c.status)}">${titleCase(c.status)}</span></td>
        <td><span class="pill ${c.is_holdout ? "holdout" : ""}">${c.is_holdout ? "control" : "treated"}</span></td>
        <td><span class="pill ${c.synthetic ? "synthetic" : "live"}">${c.synthetic ? "synthetic" : "live test"}</span></td>
        <td class="muted">${shortTime(c.updated_at)}</td>
      </tr>`)
    : html`<tr><td colspan="9" class="empty">No cases match these filters.</td></tr>`);

  $$("#cases-table tbody tr[data-id]").forEach((tr) =>
    tr.addEventListener("click", () => openCase(tr.dataset.id)));
}

function fillFilters() {
  const statuses = [...new Set(state.cases.map((c) => c.status))].sort();
  const cats = [...new Set(state.cases.map((c) => c.category).filter(Boolean))].sort();
  setHTML("#f-status", html`<option value="">all</option>${
    statuses.map((s) => html`<option value="${s}">${titleCase(s)}</option>`)}`);
  setHTML("#f-category", html`<option value="">all</option>${
    cats.map((s) => html`<option value="${s}">${s}</option>`)}`);
}

/* ======================================================= case file drawer === */
function receiptChips(checks) {
  if (!checks) return "";
  return Object.entries(checks).map(([phase, marks]) => {
    const items = Object.entries(marks)
      .filter(([k]) => k !== "phase")
      .map(([code, val]) => {
        const cls = val === "pass" ? "" : val === "not_applicable" ? "na"
          : String(val).startsWith("deferred") ? "defer" : "violated";
        const label = val === "pass" ? code
          : val === "not_applicable" ? `${code} n/a` : `${code} ${val}`;
        return html`<span class="rcpt ${cls}">${label}</span>`;
      });
    return html`<div class="receipts"><span class="rcpt-phase">${phase.replace("_", "-")}</span>${items}</div>`;
  });
}

function renderDecisions(decisions) {
  if (!decisions.length)
    return html`<div class="empty">No intervention was ever decided for this case.</div>`;
  return decisions.map((d) => {
    const x = d.execution;
    return html`
    <div class="dec">
      <div class="dec-top">
        <span class="n">attempt ${d.attempt_number}</span>
        <span class="pill">${titleCase(d.action)}</span>
        <span class="muted">policy cell <code>${d.policy_row_ref}</code></span>
        <span class="pill ${d.status === "executed" ? "recovered" : "stopped"}">${titleCase(d.status)}</span>
      </div>
      <div class="kv">
        <div><span>decided</span>${shortTime(d.decided_at)}</div>
        <div><span>scheduled for</span>${shortTime(d.scheduled_for)}</div>
        <div><span>executed via</span>${x ? x.mode : "—"}</div>
        <div><span>copy source</span>${x && x.copy_source ? titleCase(x.copy_source) : "—"}</div>
      </div>
      ${receiptChips(d.invariant_check)}
      ${x && x.message_copy ? html`
        <details><summary>Simulated message — composed, validated, never transmitted</summary>
          <pre class="copy">${x.message_copy}</pre>
          <details><summary>validator result</summary>
            <pre>${JSON.stringify(x.copy_validation, null, 2)}</pre></details>
        </details>` : ""}
      ${x ? html`<details><summary>execution payload</summary>
        <pre>${JSON.stringify(x.result_payload, null, 2)}</pre></details>` : ""}
    </div>`;
  });
}

function renderTrail(trail) {
  return html`<div class="trail">${trail.map((e) => html`
    <div class="trail-row actor-${e.actor} stage-${e.stage}">
      <div class="mono muted">${e.seq}</div>
      <div class="mono muted">${shortTime(e.created_at)}</div>
      <div class="stage">${e.stage}</div>
      <div><span class="pill ${e.actor === "llm" ? "llm" : ""}">${e.actor}</span></div>
      <div>
        ${e.summary}
        <details><summary>detail</summary><pre>${JSON.stringify(e.detail, null, 2)}</pre></details>
      </div>
    </div>`)}</div>`;
}

async function openCase(caseId) {
  const d = await get(`/api/cases/${encodeURIComponent(caseId)}`);
  const c = d.case;
  const holdout = !!c.is_holdout;
  $("#detail-id").textContent = c.id;
  setHTML("#detail-body", html`
    ${holdout ? html`<div class="banner" style="margin-bottom:16px"><span>⚗</span><div>
      <b>Control arm.</b> This case was deliberately never intervened on, so its outcome measures what
      happens <em>without</em> the agent. The decision stage below records the action that was withheld.
    </div></div>` : ""}
    <div class="kv">
      <div><span>status</span><span class="pill ${statusClass(c.status)}">${titleCase(c.status)}</span></div>
      <div><span>cause</span>${c.current_category ?? "—"}</div>
      <div><span>amount at risk</span>${fmtRupees(c.amount_at_risk_paise)}</div>
      <div><span>attempts used</span>${attemptDots(c.attempt_count, d.bounds.max_attempts)}
        <span class="muted"> ${c.attempt_count} of ${d.bounds.max_attempts}</span></div>
      <div><span>subscription</span><span class="mono">${c.subscription_id}</span></div>
      <div><span>customer</span><span class="mono">${c.customer_id}</span></div>
      <div><span>opted out</span>${c.customer_opted_out ? "yes" : "no"}</div>
      <div><span>arm</span>${holdout ? "control (held out)" : "treated"}</div>
      <div><span>opened</span>${shortTime(c.created_at)}</div>
      <div><span>closed</span>${shortTime(c.closed_at)}</div>
      <div><span>last contact</span>${shortTime(c.last_contact_at)}</div>
      <div><span>events received</span>${d.events.length}</div>
    </div>
    <div class="subhead">Decisions &amp; executions</div>
    ${renderDecisions(d.decisions)}
    <div class="subhead">Audit trail — append-only, ${d.audit_trail.length} entries</div>
    ${renderTrail(d.audit_trail)}`);
  $("#drawer").classList.add("show");
  $("#drawer-back").classList.add("show");
}

function closeCase() {
  $("#drawer").classList.remove("show");
  $("#drawer-back").classList.remove("show");
}

/* ================================================================== boot === */
async function load() {
  const [summary, categories, caseRows, mechanism] = await Promise.all([
    get("/api/summary"), get("/api/categories"), get("/api/cases"), get("/api/mechanism"),
  ]);
  state.summary = summary;
  state.cases = caseRows;
  state.mechanism = mechanism;

  renderChips(summary);
  renderBanner(summary);
  renderTiles(summary);
  renderOutcomeSplit(summary);
  renderEffectMini(summary);
  renderEffect(summary);
  renderFlow(mechanism);
  renderGuards(mechanism);
  renderPolicy(mechanism);
  renderBoundary(summary, mechanism);
  renderCategories(categories);
  fillFilters();
  renderCases();
}

$("#refresh").addEventListener("click", load);
$("#close-detail").addEventListener("click", closeCase);
$("#drawer-back").addEventListener("click", closeCase);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCase(); });
["#f-status", "#f-category", "#f-arm"].forEach((s) => $(s).addEventListener("change", renderCases));
$("#f-text").addEventListener("input", renderCases);
window.addEventListener("hashchange", route);

$("#expand").addEventListener("click", () => {
  const anyClosed = $$("details").some((d) => !d.open);
  $$("details").forEach((d) => (d.open = anyClosed));
  $("#expand").textContent = anyClosed ? "Collapse all detail" : "Expand all detail";
});

route();
load().catch((err) => {
  setHTML("#tiles", html`<div class="tile" style="grid-column:1/-1">
    <div class="label">Dashboard could not load</div>
    <div class="sub">${err.message} — is the API running, and has the batch been run?</div></div>`);
});
