/* Dashboard client (docs/06). Vanilla fetch, no framework, no build step.
   A Refresh button re-fetches; there is deliberately no polling and no websocket. */

const $ = (sel) => document.querySelector(sel);
const state = { cases: [], summary: null };

const fmtRupees = (paise) =>
  "₹" + (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const pct = (x) => (x * 100).toFixed(1) + "%";
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const shortTime = (iso) => (iso ? iso.replace("T", " ").replace("Z", "") : "—");

const statusClass = (s) =>
  s === "recovered" ? "recovered" : s === "open" ? "open" : "stopped";

async function get(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(path + " -> " + res.status);
  return res.json();
}

/* ------------------------------------------------------- summary + banner */
function renderTiles(s) {
  const rate = s.recovery_rate;
  const stoppedTotal = s.stopped.total;
  const ttr = s.avg_time_to_recovery_hours;
  const tiles = [
    { cls: "", label: "₹ at risk", value: fmtRupees(s.total_at_risk_paise),
      sub: `${s.n_cases} cases` },
    { cls: "recovered", label: "₹ recovered", value: fmtRupees(s.total_recovered_paise),
      sub: `${pct(rate.rate)} of closed cases` },
    { cls: "", label: "Recovery rate", value: pct(rate.rate),
      sub: `${rate.numerator} / ${rate.denominator} closed · strict ${pct(rate.strict_rate)}` },
    { cls: "", label: "Avg time to recovery",
      value: ttr === null ? "—" : (ttr / 24).toFixed(1) + " d",
      sub: ttr === null ? "no recoveries yet" : `${ttr} h (${s.time_basis})` },
    /* Stopped sits beside ₹ recovered at equal visual weight, on purpose (docs/07 §4). */
    { cls: "stopped", label: "Stopped & handed off", value: String(stoppedTotal),
      sub: "full case file per case" },
    { cls: "llm", label: "LLM involvement",
      value: `${s.llm.classified} + ${s.llm.drafted}`,
      sub: `classified · copy drafts (${s.llm.fallback_to_template} template fallbacks)` },
  ];
  $("#tiles").innerHTML = tiles.map((t) => `
    <div class="tile ${t.cls}">
      <div class="label">${esc(t.label)}</div>
      <div class="value">${esc(t.value)}</div>
      <div class="sub">${esc(t.sub)}</div>
    </div>`).join("");

  const b = s.bounds;
  $("#bounds").textContent =
    `max ${b.max_attempts} attempts · ${b.cooldown_hours}h cooldown · ` +
    `${b.episode_window_days}d window · LLM ${b.llm_model || b.llm_provider}`;
}

function renderBanner(s) {
  const seed = s.seed === null || s.seed === undefined ? "—" : s.seed;
  $("#banner").innerHTML =
    `<strong>⚠ Synthetic demo data</strong> — ${s.n_synthetic} of ${s.n_cases} cases are ` +
    `generated test cases (seed ${esc(seed)}); ${s.n_live} are Razorpay test-mode transactions. ` +
    `No real customers, and no message was ever transmitted — all notifications are composed, ` +
    `validated and logged only. Amounts are test-mode INR. ` +
    `Time-to-recovery uses the ${esc(s.time_basis)}.`;
}

/* ------------------------------------------------------------- categories */
function renderCategories(rows) {
  const max = Math.max(0.0001, ...rows.map((r) => r.recovery_rate));
  $("#categories tbody").innerHTML = rows.map((r) => `
    <tr>
      <td class="mono">${esc(r.category)}</td>
      <td class="num">${r.n_cases}</td>
      <td class="num">${r.n_recovered}</td>
      <td class="num">${pct(r.recovery_rate)}</td>
      <td class="num">${fmtRupees(r.recovered_paise)}</td>
      <td class="num">${r.n_stopped}</td>
      <td class="bar-col"><div class="bar"><span style="width:${(r.recovery_rate / max) * 100}%"></span></div></td>
    </tr>`).join("");
}

/* ------------------------------------------------------------- case list */
function renderCases() {
  const st = $("#f-status").value, cat = $("#f-category").value;
  const rows = state.cases.filter(
    (c) => (!st || c.status === st) && (!cat || c.category === cat));
  $("#case-count").textContent = `${rows.length} of ${state.cases.length}`;
  $("#cases tbody").innerHTML = rows.map((c) => `
    <tr data-id="${esc(c.case_id)}">
      <td class="mono">${esc(c.case_id)}</td>
      <td class="mono">${esc(c.subscription_id)}</td>
      <td>${esc(c.category ?? "—")}</td>
      <td class="num">${fmtRupees(c.amount_at_risk_paise)}</td>
      <td class="num">${c.attempt_count}</td>
      <td><span class="pill ${statusClass(c.status)}">${esc(c.status)}</span></td>
      <td><span class="pill ${c.synthetic ? "synthetic" : "live"}">${c.synthetic ? "synthetic" : "live test"}</span></td>
      <td class="muted">${esc(shortTime(c.updated_at))}</td>
    </tr>`).join("");
  document.querySelectorAll("#cases tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => openCase(tr.dataset.id)));
}

function fillFilters() {
  const statuses = [...new Set(state.cases.map((c) => c.status))].sort();
  const cats = [...new Set(state.cases.map((c) => c.category).filter(Boolean))].sort();
  $("#f-status").innerHTML =
    '<option value="">all</option>' + statuses.map((s) => `<option>${esc(s)}</option>`).join("");
  $("#f-category").innerHTML =
    '<option value="">all</option>' + cats.map((s) => `<option>${esc(s)}</option>`).join("");
}

/* ---------------------------------------------------- case file drill-down */
function renderTrail(trail) {
  return `<div class="trail">` + trail.map((e) => `
    <div class="trail-row actor-${esc(e.actor)}">
      <div class="mono muted">${e.seq}</div>
      <div class="mono muted">${esc(shortTime(e.created_at))}</div>
      <div class="stage">${esc(e.stage)}</div>
      <div><span class="pill ${e.actor === "llm" ? "llm" : ""}">${esc(e.actor)}</span></div>
      <div>
        ${esc(e.summary)}
        <details><summary>detail</summary><pre>${esc(JSON.stringify(e.detail, null, 2))}</pre></details>
      </div>
    </div>`).join("") + `</div>`;
}

function renderDecisions(decisions) {
  if (!decisions.length) return '<p class="muted">No intervention was ever decided for this case.</p>';
  return decisions.map((d) => {
    const x = d.execution;
    return `
    <div style="border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px">
      <div class="kv">
        <div><span>attempt</span>${d.attempt_number}</div>
        <div><span>action</span>${esc(d.action)}</div>
        <div><span>policy cell</span><code>${esc(d.policy_row_ref)}</code></div>
        <div><span>status</span>${esc(d.status)}</div>
        <div><span>decided</span>${esc(shortTime(d.decided_at))}</div>
        <div><span>scheduled for</span>${esc(shortTime(d.scheduled_for))}</div>
        <div><span>executed via</span>${x ? esc(x.mode) : "—"}</div>
        <div><span>copy source</span>${x && x.copy_source ? esc(x.copy_source) : "—"}</div>
      </div>
      <details><summary>invariant receipts</summary>
        <pre>${esc(JSON.stringify(d.invariant_check, null, 2))}</pre></details>
      ${x && x.message_copy ? `<details><summary>simulated message (never transmitted)</summary>
        <pre>${esc(x.message_copy)}</pre>
        <pre>${esc(JSON.stringify(x.copy_validation, null, 2))}</pre></details>` : ""}
      ${x ? `<details><summary>execution payload</summary>
        <pre>${esc(JSON.stringify(x.result_payload, null, 2))}</pre></details>` : ""}
    </div>`;
  }).join("");
}

async function openCase(caseId) {
  const d = await get(`/api/cases/${encodeURIComponent(caseId)}`);
  const c = d.case;
  $("#detail-id").textContent = c.id;
  $("#detail-body").innerHTML = `
    <div class="kv">
      <div><span>status</span><span class="pill ${statusClass(c.status)}">${esc(c.status)}</span></div>
      <div><span>category</span>${esc(c.current_category ?? "—")}</div>
      <div><span>amount at risk</span>${fmtRupees(c.amount_at_risk_paise)}</div>
      <div><span>attempts used</span>${c.attempt_count} of ${d.bounds.max_attempts}</div>
      <div><span>subscription</span><span class="mono">${esc(c.subscription_id)}</span></div>
      <div><span>customer</span><span class="mono">${esc(c.customer_id)}</span></div>
      <div><span>opted out</span>${c.customer_opted_out ? "yes" : "no"}</div>
      <div><span>data</span>${c.synthetic ? "synthetic" : "live test mode"}</div>
      <div><span>opened</span>${esc(shortTime(c.created_at))}</div>
      <div><span>closed</span>${esc(shortTime(c.closed_at))}</div>
      <div><span>last contact</span>${esc(shortTime(c.last_contact_at))}</div>
      <div><span>events received</span>${d.events.length}</div>
    </div>
    <div class="subhead">Decisions &amp; executions</div>
    ${renderDecisions(d.decisions)}
    <div class="subhead">Audit trail (append-only, ${d.audit_trail.length} entries)</div>
    ${renderTrail(d.audit_trail)}`;
  $("#detail").classList.remove("hidden");
  $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ----------------------------------------------------------------- boot */
async function load() {
  const [summary, categories, cases] = await Promise.all([
    get("/api/summary"), get("/api/categories"), get("/api/cases"),
  ]);
  state.summary = summary;
  state.cases = cases;
  renderTiles(summary);
  renderBanner(summary);
  renderCategories(categories);
  fillFilters();
  renderCases();
}

$("#refresh").addEventListener("click", load);
$("#close-detail").addEventListener("click", () => $("#detail").classList.add("hidden"));
$("#f-status").addEventListener("change", renderCases);
$("#f-category").addEventListener("change", renderCases);

load().catch((err) => {
  $("#tiles").innerHTML =
    `<div class="tile" style="grid-column:1/-1"><div class="label">Dashboard could not load</div>
     <div class="sub">${esc(err.message)} — is the API running, and has the batch been run?</div></div>`;
});
