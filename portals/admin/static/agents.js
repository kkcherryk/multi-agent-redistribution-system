// Host portal, Phase 6: watch the backend agents, food at risk, backup requests and the agent message log.
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "—");
  const STAGE = { widened: "Wider alert sent", backup_requested: "Backup collector asked", needs_host: "Needs host attention",
                  resolved: "Resolved" };
  const TOPIC = {
    collectors_ranked: (p) => `Ranked collectors: ${(p.ranking || []).map((r) => `${r.name} ~${Math.round(r.eta_minutes)} min`).join(", ") || "none available"}`,
    recommended_pickup: (p) => `Recommended this food to an agent (rank ${p.rank}, ~${Math.round(p.eta_minutes)} min)`,
    food_at_risk: (p) => `Food at risk (${(p.reasons || []).join("; ")}) — alerted ${p.newly_alerted} more agent(s) within ${p.radius_km} km`,
    rank_backup: () => "Asked logistics to rank backup collectors",
    backup_request: (p) => `Offered backup collection (~${Math.round(p.eta_minutes)} min away)`,
    backup_requested: (p) => `Backup collector asked: ${p.reason}`,
    backup_timed_out: () => "Backup collector did not answer in time",
    backup_accepted: () => "Backup collector accepted",
    needs_host_attention: (p) => `Needs host attention: ${p.reason}`,
    escalation_resolved: (p) => `Escalation closed (food is now ${p.outcome})`,
  };

  function td(ui, content) {
    const c = ui.el("td");
    if (content instanceof Node) c.append(content); else c.textContent = content ?? "—";
    return c;
  }

  async function load(ui) {
    const data = await ui.api("/agent-system");
    const status = $("agents-status");
    status.replaceChildren();
    for (const a of data.agents) {
      const card = ui.el("div", { class: "card" });
      card.append(ui.el("strong", {}, a.name === "logistics" ? "Logistics agent" : "Backup collection agent"));
      card.append(ui.el("div", { class: "meta" }, a.description));
      card.append(ui.el("div", { class: "meta" }, `Last run: ${fmt(a.last_tick)}` + (a.last_error ? ` — error: ${a.last_error}` : "")));
      status.append(card);
    }
    status.append(ui.el("p", { class: "note" }, `${data.backup_volunteers} NGO(s) offer backup collections.`));

    const tb = $("escalations");
    tb.replaceChildren();
    for (const e of data.escalations) {
      const tr = ui.el("tr");
      const reqs = e.backup_requests.map((q) => `${q.volunteer}: ${q.status}`).join(", ") || "—";
      tr.append(td(ui, fmt(e.updated_at)), td(ui, e.restaurant), td(ui, e.food), td(ui, STAGE[e.stage] || e.stage),
                td(ui, e.reason), td(ui, reqs), td(ui, ui.statusChip(e.listing_status)));
      tb.append(tr);
    }
    if (!data.escalations.length) { const tr = ui.el("tr"); tr.append(td(ui, "No food at risk.")); tb.append(tr); }

    const log = $("agent-log");
    log.replaceChildren();
    for (const m of data.messages) {
      const text = (TOPIC[m.topic] || (() => m.topic))(m.payload || {});
      const to = m.recipient === "host" ? "host" : (m.recipient.length === 32 ? "an NGO agent" : m.recipient);
      log.append(ui.el("div", { class: "agent-msg" }, `${fmt(m.created_at)} — ${m.sender} → ${to}: ${text}`));
    }
    if (!data.messages.length) log.append(ui.el("p", { class: "note" }, "No agent activity yet."));
  }

  window.FRSAgents = {
    wired: false,
    show(ui) {
      if (!this.wired) {
        this.wired = true;
        $("run-agents").addEventListener("click", async () => {
          const b = $("run-agents");
          b.disabled = true;
          try { await ui.api("/agent-system/run", { method: "POST" }); await load(ui); } finally { b.disabled = false; }
        });
      }
      return load(ui).catch((e) => { $("agent-log").textContent = e.message; });
    },
  };
})();
