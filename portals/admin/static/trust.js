// Host portal, Phase 7: trust scores with confidence, reasons, review flags and host notes (Module 12).
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "short" });

  function scoreBadge(ui, t) {
    const color = t.confidence === "low" ? "grey" : t.score >= 70 ? "green" : t.score >= 40 ? "orange" : "red";
    return ui.el("span", { class: "badge " + color, title: t.method }, `${Math.round(t.score)} / 100`);
  }

  function card(ui, row, reload) {
    const c = ui.el("div", { class: "card" });
    const head = ui.el("div", { class: "row" });
    head.append(ui.el("strong", {}, row.name), scoreBadge(ui, row),
                ui.el("span", { class: "pill" }, `${row.confidence} confidence · based on ${row.based_on}`));
    c.append(head);
    const ul = ui.el("ul", { class: "checklist" });
    row.reasons.forEach((r) => ul.append(ui.el("li", {}, r)));
    c.append(ul);
    for (const f of row.flags) c.append(ui.el("div", { class: "callout err" }, "Review: " + f));
    for (const n of row.notes) c.append(ui.el("div", { class: "meta" }, `Host note (${fmt(n.created_at)}): ${n.note}`));
    const row2 = ui.el("div", { class: "row", style: "margin-top:8px" });
    const input = ui.el("input", { maxlength: "2000", placeholder: "Add a note (e.g. spoke to the NGO, issue resolved)",
                                   "aria-label": `Note for ${row.name}`, style: "flex:1" });
    const add = ui.el("button", { type: "button", class: "small" }, "Add note");
    const msg = ui.el("div", { class: "msg" });
    add.addEventListener("click", async () => {
      try {
        await ui.api(`/trust/${row.account_id}/notes`, { method: "POST", body: JSON.stringify({ note: input.value }) });
        await reload();
      } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
    });
    row2.append(input, add);
    c.append(row2, msg);
    return c;
  }

  async function load(ui) {
    const data = await ui.api("/trust");
    const reload = () => load(ui);
    for (const [key, el] of [["ngos", "trust-ngos"], ["restaurants", "trust-restaurants"]]) {
      const box = $(el);
      box.replaceChildren();
      if (!data[key].length) box.append(ui.el("p", { class: "note" }, "No verified accounts yet."));
      data[key].forEach((row) => box.append(card(ui, row, reload)));
    }
  }

  window.FRSTrust = { show(ui) { return load(ui).catch((e) => { $("trust-ngos").textContent = e.message; }); } };
})();
