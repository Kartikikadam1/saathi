/* Saathi caregiver dashboard — vanilla JS, polls the API. */
(() => {
  const params = new URLSearchParams(location.search);
  const S = { pid: params.get("patient") || "P-001", view: "today", overview: null, drawerSid: null, traceFilter: null, config: null };
  const $ = (id) => document.getElementById(id);

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const time = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  const dt = (iso) => iso ? new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
  const STATE_LABEL = { taken: "Taken", missed: "Missed", uncertain: "Unconfirmed", pending: "Not checked yet", double_dose_reported: "Possible double dose" };
  const OUTCOME = {
    completed: "Completed", completed_with_concerns: "Completed — concerns raised", emergency: "Emergency escalated",
    unreachable: "No answer", blocked_no_consent: "Not called — no consent", already_confirmed: "Already confirmed",
    nothing_due: "Nothing due", ended_early: "Ended early", timed_out: "Timed out", max_turns: "Completed (turn limit)",
  };
  const STATUS = { ringing: "Ringing", retry_wait: "Waiting to retry", in_progress: "On the call", planning: "Preparing" };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  }
  function toast(msg) {
    const t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 3200);
  }

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll("nav.tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  function show(view) {
    S.view = view;
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
    document.querySelectorAll("section.view").forEach((s) => s.classList.toggle("active", s.id === "view-" + view));
    refresh();
  }

  // ---------------------------------------------------------------- today
  function sessionLine(s) {
    if (!s) return "No call yet today";
    if (STATUS[s.status]) return `${STATUS[s.status]} · attempt ${s.attempts} of ${s.max_attempts}`;
    return `${OUTCOME[s.outcome] || s.outcome || s.status} at ${time(s.ended_at || s.created_at)}`;
  }

  function renderToday(ov) {
    const p = ov.patient;
    $("pt-name").textContent = p.name;
    $("pt-meta").textContent = `${p.age} yrs · ${p.doctor_name}`;
    const all = [...ov.slots.morning.meds, ...ov.slots.evening.meds];
    const missed = all.filter((m) => m.status === "missed").length;
    const taken = all.filter((m) => m.status === "taken").length;
    const first = p.name.split(" ")[0];
    let head;
    if (ov.active_session) head = ov.active_session.status === "in_progress" ? `Saathi is talking to ${first} now` : `Saathi is calling ${first}`;
    else if (ov.open_alerts.some((a) => a.severity === "critical")) head = `${first} needs help — see the urgent alert`;
    else if (missed) head = `${first} missed ${missed} dose${missed > 1 ? "s" : ""} today`;
    else if (taken === all.length && all.length) head = `${first} has taken every dose today`;
    else head = `${first} has taken ${taken} of ${all.length} doses today`;
    $("headline").textContent = head;
    $("subline").textContent = new Date(ov.today + "T00:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });

    $("strips").innerHTML = ["morning", "evening"].map((slot) => {
      const sl = ov.slots[slot];
      const times = [...new Set(sl.meds.map((m) => m.time))].join(", ");
      const busy = !!ov.active_session;
      return `<div class="strip">
        <div class="strip-head"><div><h2>${slot === "morning" ? "Morning" : "Evening"}</h2><span class="when">${esc(times)}</span></div>
          <button class="btn primary sm" data-call="${slot}" ${busy ? "disabled" : ""}>Call ${esc(first)} now</button></div>
        <div class="pockets">${sl.meds.map((m) => `<div class="pocket ${m.status}" title="${esc(m.log_id || "")}">
            <span class="pill" aria-hidden="true"></span>
            <div class="med"><strong>${esc(m.name)}</strong><span>${esc(m.dose)}</span><br>
              <span class="state ${m.status}">${STATE_LABEL[m.status] || m.status}</span></div></div>`).join("") || '<p class="muted">No medicines in this slot.</p>'}</div>
        <div class="strip-foot"><span class="muted">${esc(sessionLine(sl.session))}</span>
          ${sl.session ? `<button class="btn quiet sm" data-open="${sl.session.id}">View call</button>` : ""}</div>
      </div>`;
    }).join("");

    renderLive(ov.active_session);

    // organiser
    const days = ov.grid.days;
    let html = '<span></span>' + days.map((d) => {
      const dd = new Date(d + "T00:00");
      return `<span class="day ${d === ov.today ? "today" : ""}">${d === ov.today ? "Today" : dd.toLocaleDateString([], { weekday: "short" })}</span>`;
    }).join("");
    for (const slot of ["morning", "evening"]) {
      html += `<span class="rowlabel">${slot === "morning" ? "Morning" : "Evening"}</span>`;
      html += days.map((d) => {
        const sts = (ov.grid.data[d] || {})[slot] || [];
        let cls = "", label = "";
        if (sts.length) {
          if (sts.every((x) => x === "taken")) { cls = "taken"; label = "✓"; }
          else if (sts.includes("missed")) { cls = "missed"; label = "✕"; }
          else { cls = sts.every((x) => x === "uncertain") ? "uncertain" : "mixed"; label = "?"; }
        }
        return `<span class="lid ${cls}" title="${esc(d + " " + slot + ": " + (sts.join(", ") || "no record"))}">${label}</span>`;
      }).join("");
    }
    $("organiser").innerHTML = html;
    $("adherence").textContent = ov.adherence_7d_pct != null ? `${ov.adherence_7d_pct}% of doses taken in 7 days` : "";

    // alerts
    $("alert-count").textContent = ov.open_alerts.length ? `${ov.open_alerts.length} open` : "";
    $("alerts").innerHTML = ov.open_alerts.length ? ov.open_alerts.map((a) => `
      <div class="alert ${a.severity}">
        <p>${esc(a.message.replace(/^\[Saathi\]\s*/, ""))}</p>
        <div class="meta"><span><span class="sev ${a.severity}">${a.severity}</span> · ${esc(a.type.replace(/_/g, " "))} · ${dt(a.created_at)}</span>
          <span>${a.session_id ? `<button class="btn quiet sm" data-open="${a.session_id}">View call</button>` : ""}
          <button class="btn sm" data-ack="${a.id}">Mark as handled</button></span></div>
      </div>`).join("") : '<p class="empty">Nothing needs attention right now.</p>';

    $("recent").innerHTML = ov.recent_sessions.length ? `<div class="table-wrap"><table><tbody>${ov.recent_sessions.map((s) => `
      <tr class="rowlink" data-open="${s.id}"><td>${dt(s.created_at)}</td><td>${esc(s.slot)}</td><td>${esc(STATUS[s.status] || OUTCOME[s.outcome] || s.status)}</td></tr>`).join("")}</tbody></table></div>`
      : '<p class="empty">No calls yet. Use “Call now” on a medicine strip to start one.</p>';
    $("ap-count").textContent = ov.pending_approvals.length || "";
  }

  let liveTurnsFor = null;
  async function renderLive(s) {
    const el = $("live-panel");
    if (!s) { el.classList.add("hidden"); liveTurnsFor = null; return; }
    el.classList.remove("hidden");
    let extra = "";
    if (s.status === "ringing") extra = `<button class="btn sm" data-noanswer="${s.id}">Simulate no answer</button>`;
    if (s.status === "retry_wait") extra = `<span class="muted small">Next try at ${time(s.next_ring_at)}</span> <button class="btn sm" data-retry="${s.id}">Retry now</button>`;
    const d = await api(`/api/sessions/${s.id}`).catch(() => null);
    const turns = d ? d.turns : [];
    el.innerHTML = `<div class="panel-head"><h2><span class="pulse"></span>${esc(STATUS[s.status] || s.status)} — ${esc(s.slot)} check-in</h2>
      <span style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap"><span class="muted small">Attempt ${s.attempts} of ${s.max_attempts}</span>${extra}
      <a class="btn sm" href="/phone?patient=${encodeURIComponent(S.pid)}" target="saathi-phone">Answer on elder's phone</a>
      <button class="btn quiet sm" data-open="${s.id}">Trace</button></span></div>
      <div class="transcript">${turns.map(bubble).join("") || '<p class="muted">Waiting for the call to be answered…</p>'}</div>`;
  }

  function bubble(t) {
    const who = t.speaker === "agent" ? "Saathi" : t.speaker === "patient" ? "Elder" : "";
    const tr = t.translation_en && t.translation_en !== t.text ? `<span class="tr">${esc(t.translation_en)}</span>` : "";
    return `<div class="bubble ${t.speaker}">${who ? `<span class="who-l">${who}</span>` : ""}${esc(t.text)}${tr}</div>`;
  }

  // ---------------------------------------------------------------- calls
  async function renderCalls() {
    const rows = await api(`/api/sessions?patient_id=${encodeURIComponent(S.pid)}`);
    $("calls-body").innerHTML = rows.map((s) => `<tr class="rowlink" data-open="${s.id}">
      <td class="id">${s.id}</td><td>${dt(s.created_at)}</td><td>${esc(s.slot)}</td><td>${s.attempts}/${s.max_attempts}</td>
      <td>${esc(STATUS[s.status] || OUTCOME[s.outcome] || s.status)}</td><td class="small">${esc(s.summary || "")}</td></tr>`).join("")
      || '<tr><td colspan="6" class="empty">No calls yet.</td></tr>';
  }

  // ---------------------------------------------------------------- approvals
  const ACTION = {
    share_report_with_doctor: "Send a symptom report to the doctor",
    update_medication_schedule: "Change the medicine schedule",
  };
  async function renderApprovals() {
    const rows = await api(`/api/approvals?patient_id=${encodeURIComponent(S.pid)}`);
    $("approvals").innerHTML = rows.length ? rows.map((a) => {
      const p = a.payload || {};
      const detail = a.action_type === "share_report_with_doctor"
        ? `<p style="margin-top:.4rem">To ${esc(p.doctor)} (${esc(p.doctor_contact)}):</p><p class="muted">${esc(p.report)}</p>`
        : `<p style="margin-top:.4rem">${esc(p.med_name ? (p.proposed_change === "deactivate" ? `Stop ${p.med_name} in the schedule` : `Review ${p.med_name}`) : "Review the schedule")}.</p><p class="muted">Elder said: “${esc(p.elder_statement)}”</p>`;
      const done = a.status !== "pending";
      return `<div class="alert ${done ? "" : "medium"}" style="margin-bottom:.75rem">
        <h3>${esc(ACTION[a.action_type] || a.action_type)}</h3>${detail}
        <div class="meta"><span>${esc(a.reason)} · requested ${dt(a.requested_at)} · <span class="id">${a.id}</span>
          ${done ? ` · <strong>${esc(a.status)}</strong> by ${esc(a.decided_by)}` : ""}</span>
          <span>${a.session_id ? `<button class="btn quiet sm" data-open="${a.session_id}">View call</button>` : ""}
          ${done ? "" : `<button class="btn sm danger" data-decide="reject" data-ap="${a.id}">Reject</button>
          <button class="btn sm good" data-decide="approve" data-ap="${a.id}">Approve</button>`}</span></div></div>`;
    }).join("") : '<p class="empty">No actions are waiting. Saathi asks here before anything that cannot be undone.</p>';
  }

  // ---------------------------------------------------------------- tests
  async function renderTests(batch) {
    const defs = await api("/api/tests");
    batch = batch || await api("/api/tests/latest");
    const byId = Object.fromEntries((batch.results || []).map((r) => [r.tc_id, r]));
    $("tests-meta").textContent = batch.batch_id ? `Last run: ${batch.passed} of ${batch.total} passed · batch ${batch.batch_id}` : "Not run yet.";
    $("dl-report").classList.toggle("hidden", !batch.batch_id);
    $("tests-body").innerHTML = defs.map((d) => {
      const r = byId[d.id];
      const checks = r ? `<ul class="checks">${r.checks.map((c) => `<li class="${c.passed ? "ok" : "no"}">${esc(c.check)}${c.detail && !c.passed ? ` <span class="muted">(${esc(c.detail)})</span>` : ""}</li>`).join("")}</ul>` : "";
      return `<tr><td><strong>${d.id}</strong><br>${esc(d.name)}${d.official ? '<br><span class="official">Official case</span>' : ""}</td>
        <td class="small">${esc(d.input)}</td><td class="small">${esc(d.expected)}</td>
        <td>${r ? `<span class="${r.passed ? "pass" : "fail"}">${r.passed ? "Pass" : "Fail"}</span>${checks}` : '<span class="muted">Not run</span>'}</td>
        <td>${r ? `<button class="btn sm" data-open="${r.session_id}">Open evidence</button>` : ""}</td></tr>`;
    }).join("");
  }
  $("run-tests").addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Running…";
    try { const b = await api("/api/tests/run", { method: "POST", body: {} }); await renderTests(b); toast(`${b.passed} of ${b.total} test cases passed`); }
    catch (err) { toast(err.message); }
    e.target.disabled = false; e.target.textContent = "Run all test cases";
  });

  // ---------------------------------------------------------------- messages
  async function renderMessages() {
    const rows = await api(`/api/notifications?patient_id=${encodeURIComponent(S.pid)}`);
    const CH = { sms_mock: "SMS (simulated)", telegram: "Telegram", email_mock: "Email (simulated)" };
    $("msgs-body").innerHTML = rows.map((n) => `<tr><td>${dt(n.created_at)}</td><td>${esc(CH[n.channel] || n.channel)}</td>
      <td>${esc(n.recipient)}</td><td class="small">${esc(n.message)}</td><td class="${n.status === "sent" ? "pass" : "fail"}">${esc(n.status)}</td></tr>`).join("")
      || '<tr><td colspan="5" class="empty">No messages sent yet.</td></tr>';
  }

  // ---------------------------------------------------------------- setup
  async function renderSetup() {
    const prof = await api(`/api/caregiver/patients/${encodeURIComponent(S.pid)}`);
    const c = prof.consent || {};
    $("sw-consent").checked = !!c.call_consent;
    $("consent-meta").textContent = c.call_consent ? `Given by ${c.granted_by || "—"} · ${c.id}` : `Not given · ${c.id || ""}`;
    $("sw-auto").checked = !!prof.patient.auto_calls;
    $("sel-lang").value = prof.patient.language || "en";
    $("meds-body").innerHTML = prof.medications.map((m) => `<tr><td><a class="src" href="${m.source_url}" target="_blank">${m.id}</a></td>
      <td><strong>${esc(m.name)}</strong></td><td>${esc(m.dose)}</td><td>${esc(m.slot)} · ${esc(m.time)}</td><td>${esc(m.instructions)}</td>
      <td>${m.active ? "Active" : '<span class="fail">Stopped</span>'}</td></tr>`).join("");
    $("contacts-body").innerHTML = prof.contacts.map((x) => `<tr><td><a class="src" href="${x.source_url}" target="_blank">${x.id}</a></td>
      <td><strong>${esc(x.name)}</strong><br><span class="muted small">${esc(x.relation)}</span></td><td>${esc(x.phone)}</td>
      <td class="small">${x.priority === 1 ? "All alerts" : "Urgent and high alerts"}</td>
      <td><span style="display:flex;gap:.4rem"><input type="text" size="12" value="${esc(x.telegram_chat_id || "")}" id="tg-${x.id}" aria-label="Telegram chat ID for ${esc(x.name)}">
      <button class="btn sm" data-tg="${x.id}">Save</button></span></td></tr>`).join("");
    $("rules-body").innerHTML = prof.rules.map((r) => `<tr><td><a class="src" href="${r.source_url}" target="_blank">${r.id}</a></td>
      <td>${esc(r.key.replace(/_/g, " "))}</td><td><code>${esc(r.value)}</code></td><td class="small">${esc(r.description)}</td></tr>`).join("");
  }
  $("sw-consent").addEventListener("change", async (e) => {
    await api(`/api/caregiver/patients/${S.pid}/consent`, { method: "PUT", body: { call_consent: e.target.checked } });
    toast(e.target.checked ? "Consent recorded — Saathi can call" : "Consent removed — Saathi will not call"); renderSetup();
  });
  $("sw-auto").addEventListener("change", async (e) => {
    await api(`/api/caregiver/patients/${S.pid}/settings`, { method: "PUT", body: { auto_calls: e.target.checked } });
    toast(e.target.checked ? "Automatic daily calls turned on" : "Automatic daily calls turned off");
  });
  $("sel-lang").addEventListener("change", async (e) => {
    await api(`/api/caregiver/patients/${S.pid}/settings`, { method: "PUT", body: { language: e.target.value } });
    toast("Call language saved");
  });

  // ---------------------------------------------------------------- drawer
  async function openDrawer(sid) {
    S.drawerSid = sid;
    $("drawer").classList.remove("hidden"); $("drawer-bg").classList.remove("hidden");
    await renderDrawer();
    $("drawer-close").focus();
  }
  function closeDrawer() {
    S.drawerSid = null; $("drawer").classList.add("hidden"); $("drawer-bg").classList.add("hidden");
  }
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-bg").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.drawerSid) closeDrawer(); });

  async function renderDrawer() {
    const sid = S.drawerSid; if (!sid) return;
    const [d, tr] = await Promise.all([api(`/api/sessions/${sid}`), api(`/api/sessions/${sid}/trace`)]);
    const s = d.session;
    $("drawer-title").textContent = `${s.slot === "morning" ? "Morning" : "Evening"} check-in with ${d.patient.name}`;
    $("drawer-meta").innerHTML = `<span class="id">${s.id}</span> · ${dt(s.created_at)} · ${esc(STATUS[s.status] || OUTCOME[s.outcome] || s.status)} · attempts ${s.attempts}/${s.max_attempts}`
      + ` · <a href="/api/sessions/${s.id}/trace" target="_blank">raw trace JSON</a>`;
    $("d-transcript").innerHTML = d.turns.map(bubble).join("") || '<p class="muted">No conversation — the call was not answered.</p>';
    const MED = {};
    const rec = [];
    if (s.summary) rec.push(`<p style="margin-bottom:.6rem">${esc(s.summary)}</p>`);
    if (d.dose_logs.length) rec.push(`<table><tbody>${d.dose_logs.map((l) => `<tr><td class="id">${l.id}</td><td>${esc(l.med_id)}</td>
      <td><span class="state ${l.status}">${STATE_LABEL[l.status] || l.status}</span></td><td class="small">${l.verified ? "Verified" : "Not verified"}</td></tr>`).join("")}</tbody></table>`);
    if (d.alerts.length) rec.push(`<div class="alerts" style="margin-top:.75rem">${d.alerts.map((a) => `<div class="alert ${a.severity}"><p>${esc(a.message.replace(/^\[Saathi\]\s*/, ""))}</p>
      <div class="meta"><span class="id">${a.id}</span><span>${d.notifications.filter((n) => n.alert_id === a.id).map((n) => esc(n.recipient.split(" <")[0])).filter((v, i, arr) => arr.indexOf(v) === i).join(", ") || "Dashboard only"}</span></div></div>`).join("")}</div>`);
    if (d.approvals.length) rec.push(`<p style="margin-top:.75rem"><strong>Approvals:</strong> ${d.approvals.map((a) => `${esc(ACTION[a.action_type] || a.action_type)} — ${esc(a.status)}`).join("; ")}</p>`);
    $("d-records").innerHTML = rec.join("") || '<p class="muted">Nothing recorded.</p>';

    const stages = [...new Set(tr.map((t) => t.stage))];
    $("d-filter").innerHTML = `<button aria-pressed="${!S.traceFilter}" data-stage="">All</button>` +
      stages.map((st) => `<button aria-pressed="${S.traceFilter === st}" data-stage="${st}">${st.toLowerCase()}</button>`).join("");
    const items = tr.filter((t) => !S.traceFilter || t.stage === S.traceFilter);
    $("d-trace-count").textContent = `${tr.length} steps`;
    $("d-trace").innerHTML = items.map((t) => {
      const srcs = (t.source_ids || []).map((id, i) => {
        const url = (t.source_urls || [])[i];
        return url ? `<a class="src" href="${esc(url)}" target="_blank" title="${esc(url)}">${esc(id)}</a>` : `<span class="src">${esc(id)}</span>`;
      }).join("");
      const io = (t.input && Object.keys(t.input).length) || (t.output && Object.keys(t.output).length)
        ? `<details class="io"><summary>Input and output</summary><pre>${esc(JSON.stringify({ input: t.input, output: t.output }, null, 2))}</pre></details>` : "";
      return `<li class="st-${t.stage}"><span class="stage">${t.stage}</span><span class="tool">${esc(t.tool)}</span>
        <span class="muted small"> · ${time(t.created_at)}</span><span class="sum">${esc(t.summary)}</span>
        ${srcs ? `<div class="srcs">${srcs}</div>` : ""}${io}</li>`;
    }).join("");
  }
  $("d-filter").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-stage]"); if (!b) return;
    S.traceFilter = b.dataset.stage || null; renderDrawer();
  });

  // ---------------------------------------------------------------- global clicks
  document.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open],[data-call],[data-ack],[data-decide],[data-noanswer],[data-retry],[data-tg]");
    if (!t) return;
    try {
      if (t.dataset.open) return openDrawer(t.dataset.open);
      if (t.dataset.call) {
        t.disabled = true;
        const s = await api("/api/agent/checkin", { method: "POST", body: { patient_id: S.pid, slot: t.dataset.call } });
        if (s.status === "ringing") toast("Calling now — answer on the elder's phone");
        else toast(OUTCOME[s.outcome] || s.status);
      }
      if (t.dataset.ack) { await api(`/api/alerts/${t.dataset.ack}/ack`, { method: "POST" }); toast("Marked as handled"); }
      if (t.dataset.decide) {
        const r = await api(`/api/approvals/${t.dataset.ap}/decide`, { method: "POST", body: { decision: t.dataset.decide, by: "Rahul Sharma (caregiver)" } });
        toast(t.dataset.decide === "approve" ? (r.status === "executed" ? "Approved and done" : `Approved — ${r.status}`) : "Rejected — nothing was changed");
        renderApprovals();
      }
      if (t.dataset.noanswer) { await api(`/api/agent/sessions/${t.dataset.noanswer}/simulate-no-answer`, { method: "POST" }); toast("Marked as not answered"); }
      if (t.dataset.retry) { await api(`/api/agent/sessions/${t.dataset.retry}/retry-now`, { method: "POST" }); toast("Calling again"); }
      if (t.dataset.tg) {
        await api(`/api/caregiver/contacts/${t.dataset.tg}`, { method: "PUT", body: { telegram_chat_id: $("tg-" + t.dataset.tg).value } });
        toast("Telegram chat ID saved");
      }
      refresh();
    } catch (err) { toast(err.message); }
  });

  $("reset-btn").addEventListener("click", async () => {
    if (!window.confirm("Delete all calls, alerts and test runs and restore the demo patient?")) return;
    await api("/api/admin/reset", { method: "POST" }); closeDrawer(); toast("Demo reset"); refresh();
  });

  // ---------------------------------------------------------------- loop
  let busy = false;
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const ov = await api(`/api/dashboard/${encodeURIComponent(S.pid)}/overview`);
      S.overview = ov;
      if (S.view === "today") renderToday(ov); else {
        $("ap-count").textContent = ov.pending_approvals.length || "";
        $("pt-name").textContent = ov.patient.name; $("pt-meta").textContent = `${ov.patient.age} yrs · ${ov.patient.doctor_name}`;
      }
      if (S.view === "calls") await renderCalls();
      if (S.view === "approvals") await renderApprovals();
      if (S.view === "tests" && !document.querySelector("#tests-body tr")) await renderTests();
      if (S.view === "messages") await renderMessages();
      if (S.view === "setup" && !document.querySelector("#meds-body tr")) await renderSetup();
      if (S.drawerSid && ov.active_session && ov.active_session.id === S.drawerSid) await renderDrawer();
    } catch (err) { console.error(err); }
    busy = false;
  }
  document.querySelector('[data-view="tests"]').addEventListener("click", () => renderTests());
  document.querySelector('[data-view="setup"]').addEventListener("click", () => renderSetup());

  (async () => {
    try {
      S.config = await api("/api/config");
      const chip = $("llm-chip");
      if (S.config.llm_enabled) { chip.textContent = `Gemini · ${S.config.llm_model}`; chip.classList.add("on"); }
      else chip.textContent = "Rule engine (no Gemini key)";
    } catch (e) { /* ignore */ }
    $("open-phone").href = `/phone?patient=${encodeURIComponent(S.pid)}`;
    refresh();
    setInterval(() => { if (!document.hidden) refresh(); }, 2500);
  })();
})();
