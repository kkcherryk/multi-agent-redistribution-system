// Host / Admin console: verification queue with evidence viewer, NGO agent directory, accounts, activity.
(function () {
  let ui;
  const $ = (id) => document.getElementById(id);
  const FIELD_LABELS = {
    restaurant_name: "Restaurant", applicant_name: "Applicant", licence_number: "Licence no.",
    ngo_name: "NGO", ngo_registration_id: "NGO registration ID", representative_name: "Representative",
    address: "Address", latitude: "Location", longitude: "Longitude", additional_info: "Additional info",
  };

  function td(content) {
    const cell = ui.el("td");
    if (content instanceof Node) cell.append(content); else cell.textContent = content ?? "—";
    return cell;
  }

  function mapLink(p) {
    if (!p) return "—";
    const url = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(p.latitude)}&mlon=${encodeURIComponent(p.longitude)}#map=17/${p.latitude}/${p.longitude}`;
    return ui.el("a", { href: url, target: "_blank", rel: "noopener" }, `${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}`);
  }

  function applicationCard(item) {
    const acc = item.account;
    const card = ui.el("div", { class: "card" });
    const head = ui.el("div", { class: "row" });
    head.append(ui.el("strong", {}, acc.display_name), ui.el("span", { class: "note" }, acc.role.toUpperCase()),
                ui.statusChip(acc.status), ui.badgeRow(acc.badges));
    card.append(head, ui.el("p", { class: "note" }, `${acc.email} · ${acc.phone || "no phone"}`));

    if (item.profile) {
      const dl = ui.el("dl");
      for (const [k, v] of Object.entries(item.profile)) {
        if (!(k in FIELD_LABELS) || v === null || v === "") continue;
        dl.append(ui.el("dt", {}, FIELD_LABELS[k]));
        const dd = ui.el("dd");
        if (k === "latitude") { dd.append(mapLink(item.profile)); dl.append(dd); continue; }
        if (k === "longitude") { dl.lastChild.remove(); continue; }
        dd.textContent = v;
        dl.append(dd);
      }
      card.append(dl);
    } else {
      card.append(ui.el("p", { class: "note" }, "No profile details yet."));
    }

    const thumbs = ui.el("div", { class: "thumbs" });
    for (const d of item.documents) {
      const label = (item.requirements[d.kind] || {}).label || d.kind;
      const a = ui.el("a", { href: "/api/documents/" + d.id, target: "_blank", rel: "noopener", title: d.filename });
      if (d.content_type.startsWith("image/")) a.append(ui.el("img", { src: "/api/documents/" + d.id, alt: label }));
      a.append(ui.el("div", {}, label + (d.content_type === "application/pdf" ? " (PDF)" : "")));
      thumbs.append(a);
    }
    card.append(thumbs);
    if (item.missing.length) card.append(ui.el("div", { class: "callout err" }, "Missing: " + item.missing.join(", ")));

    if (item.reviews.length) {
      const hist = ui.el("details");
      hist.append(ui.el("summary", {}, `Review history (${item.reviews.length})`));
      for (const r of item.reviews) {
        hist.append(ui.el("div", { class: "note" },
          `${new Date(r.created_at).toLocaleString()} — ${r.decision}: ${r.from_status} → ${r.to_status}${r.note ? " — " + r.note : ""}`));
      }
      card.append(hist);
    }

    if (acc.status !== "applicant") {
      const note = ui.el("textarea", { rows: "2", maxlength: "2000", placeholder: "Message to the applicant (required to hold or decline)" });
      const row = ui.el("div", { class: "row", style: "margin-top:8px" });
      const msg = ui.el("div", { class: "msg" });
      for (const [decision, label] of [["verify", "Verify"], ["hold", "Put on hold / ask for info"], ["decline", "Decline"]]) {
        const target = { verify: "verified", hold: "on_hold", decline: "declined" }[decision];
        if (target === acc.status) continue;
        const b = ui.el("button", { type: "button", class: decision === "decline" ? "danger" : "" }, label);
        b.addEventListener("click", async () => {
          try {
            await ui.api(`/accounts/${acc.id}/review`, { method: "POST", body: JSON.stringify({ decision, note: note.value || null }) });
            await loadAll();
          } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
        });
        row.append(b);
      }
      card.append(note, row, msg);
    }
    return card;
  }

  async function loadQueue() {
    const status = $("queue-status").value;
    const items = await ui.api("/review-queue?status=" + encodeURIComponent(status));
    const q = $("queue");
    q.replaceChildren();
    if (!items.length) q.append(ui.el("p", { class: "note" }, "Nothing here."));
    items.forEach((it) => q.append(applicationCard(it)));
    if (status === "under_review") $("queue-count").textContent = items.length ? `(${items.length})` : "";
  }

  async function loadAgents() {
    const rows = await ui.api("/ngo-directory");
    const tb = $("agents");
    tb.replaceChildren();
    for (const r of rows) {
      const tr = ui.el("tr");
      tr.append(td(r.account.display_name), td(r.profile && r.profile.representative_name), td(r.account.phone),
                td(ui.badge(r.account.badges.verification)), td(ui.badge(r.account.badges.availability)),
                td(mapLink(r.profile)));
      tb.append(tr);
    }
    if (!rows.length) {
      const tr = ui.el("tr");
      tr.append(td("No NGO agents yet."));
      tb.append(tr);
    }
  }

  async function loadAccounts() {
    const [accounts, events] = await Promise.all([ui.api("/accounts"), ui.api("/events?limit=40")]);
    const byId = Object.fromEntries(accounts.map((a) => [a.id, a.display_name]));
    const tb = $("accounts");
    tb.replaceChildren();
    for (const a of accounts) {
      const tr = ui.el("tr");
      tr.append(td(a.display_name), td(a.role), td(a.email), td(ui.statusChip(a.status)), td(ui.badgeRow(a.badges)),
                td(new Date(a.created_at).toLocaleString()));
      tb.append(tr);
    }
    const te = $("events");
    te.replaceChildren();
    for (const ev of events) {
      const tr = ui.el("tr");
      tr.append(td(new Date(ev.created_at).toLocaleString()), td(ev.type), td(ev.portal),
                td(byId[ev.subject_id] || ev.subject_id));
      te.append(tr);
    }
  }

  async function loadAll() {
    await Promise.all([loadQueue(), loadAgents(), loadAccounts()]);
  }

  function showTab(name) {
    for (const b of document.querySelectorAll("#admin-tabs button")) b.classList.toggle("active", b.dataset.tab === name);
    for (const t of ["queue", "agents", "accounts", "events"]) $("tab-" + t).classList.toggle("hidden", t !== name);
  }

  window.PORTAL.onSignedIn = (acc, helpers) => {
    ui = helpers;
    if (!window.__adminWired) {
      window.__adminWired = true;
      document.querySelectorAll("#admin-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
      $("queue-status").addEventListener("change", () => loadQueue());
    }
    loadAll().catch((e) => console.error(e));
  };
})();
