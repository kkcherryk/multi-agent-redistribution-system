// Host portal, Phase 10: privacy rules, retention clean-up, and per-account data export / erasure.
(function () {
  const $ = (id) => document.getElementById(id);
  const LABELS = {
    location_pings: "Pickup movement trails", stale_locations: "Last position of agents who stopped sharing",
    read_notifications: "Read notifications", unread_notifications: "Unread notifications",
    agent_messages: "Agent-to-agent messages", sign_in_events: "Sign-in records",
  };

  async function show(ui) {
    const box = $("privacy-box");
    box.replaceChildren(ui.el("p", { class: "note" }, "Loading…"));
    let policy, accounts;
    try { [policy, accounts] = await Promise.all([ui.api("/privacy"), ui.api("/accounts")]); }
    catch (e) { box.textContent = e.message; return; }
    box.replaceChildren();

    box.append(ui.el("h3", {}, "Rules the system follows"));
    const rules = ui.el("ul", { class: "checklist" });
    policy.rules.forEach((r) => rules.append(ui.el("li", {}, r)));
    box.append(rules);

    box.append(ui.el("h3", {}, "How long data is kept"));
    const tbl = ui.el("table");
    const hr = ui.el("tr");
    ["Data", "Deleted after"].forEach((h) => hr.append(ui.el("th", {}, h)));
    tbl.append(hr);
    for (const [k, d] of Object.entries(policy.retention_days)) {
      const tr = ui.el("tr");
      tr.append(ui.el("td", {}, LABELS[k] || k), ui.el("td", {}, d === 1 ? "1 day" : d + " days"));
      tbl.append(tr);
    }
    const wrap = ui.el("div", { class: "table-wrap" });
    wrap.append(tbl);
    box.append(wrap, ui.el("p", { class: "note" },
      "The housekeeping agent applies these rules every hour. Food records and the activity log are kept so totals and trust scores stay correct."));
    const purge = ui.el("button", { type: "button" }, "Run clean-up now");
    const purgeMsg = ui.el("div", { class: "msg" });
    purge.addEventListener("click", async () => {
      try {
        const r = await ui.api("/privacy/purge", { method: "POST" });
        const n = Object.values(r.deleted).reduce((a, b) => a + b, 0);
        purgeMsg.textContent = n ? `Deleted ${n} old record(s).` : "Nothing was old enough to delete.";
        purgeMsg.className = "msg ok";
      } catch (e) { purgeMsg.textContent = e.message; purgeMsg.className = "msg err"; }
    });
    box.append(purge, purgeMsg);

    box.append(ui.el("h3", {}, "Data requests for one account"));
    box.append(ui.el("p", { class: "note" },
      "Export downloads everything stored about the account. Erase removes its personal details, documents, locations and notifications for good, and signs it out; its food records stay without the personal details."));
    const sel = ui.el("select", { "aria-label": "Account" });
    accounts.filter((a) => a.role !== "host").forEach((a) => {
      const erased = a.email.endsWith("@erased.invalid");
      sel.append(ui.el("option", { value: a.id }, `${a.display_name} (${a.role})${erased ? " — erased" : " — " + a.email}`));
    });
    const row = ui.el("div", { class: "row", style: "margin-top:8px" });
    const exp = ui.el("button", { type: "button" }, "Export data");
    const erase = ui.el("button", { type: "button", class: "danger" }, "Erase account…");
    const msg = ui.el("div", { class: "msg" });
    row.append(exp, erase);
    box.append(sel, row, msg);
    if (!sel.options.length) { exp.disabled = erase.disabled = true; }

    exp.addEventListener("click", () => {
      const a = ui.el("a", { href: `/api/privacy/accounts/${encodeURIComponent(sel.value)}/export`, download: "" });
      document.body.append(a); a.click(); a.remove();
    });
    erase.addEventListener("click", async () => {
      const name = sel.options[sel.selectedIndex].textContent;
      const typed = prompt(`This permanently erases the personal data of:\n${name}\n\nIt cannot be undone. Type ERASE to confirm.`);
      if (typed === null) return;
      try {
        await ui.api(`/privacy/accounts/${encodeURIComponent(sel.value)}/erase`, { method: "POST", body: JSON.stringify({ confirm: typed.trim() }) });
        await show(ui);
        const m = $("privacy-box").querySelector(".msg:last-of-type");
        if (m) { m.textContent = "Account erased."; m.className = "msg ok"; }
      } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
    });
  }

  window.FRSPrivacy = { show };
})();
