// NGO portal, Phase 6: opt in as a backup collector and answer backup requests (Module 11).
(function () {
  let timer = null;
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

  window.PORTAL.extras = window.PORTAL.extras || [];
  window.PORTAL.extras.push((acc, ui) => {
    if (acc.status !== "verified") return;
    const root = document.getElementById("food");
    const panel = ui.el("div", { class: "panel" });
    panel.append(ui.el("h2", {}, "Backup collections"));
    panel.append(ui.el("p", { class: "note" },
      "When food is at risk of going to waste because no one has collected it, the backup agent may ask your NGO " +
      "to collect it and store it at your location. You can answer even when you are not Available for normal pickups."));
    const optRow = ui.el("div", { class: "row" });
    const toggle = ui.el("button", { type: "button" });
    const distLabel = ui.el("label", { for: "backup-km", style: "margin:0" }, "Up to");
    const dist = ui.el("select", { id: "backup-km", style: "width:auto" });
    for (const km of [5, 10, 15, 20]) dist.append(ui.el("option", { value: String(km) }, `${km} km away`));
    optRow.append(toggle, distLabel, dist);
    const msg = ui.el("div", { class: "msg" });
    const list = ui.el("div", { style: "margin-top:12px" });
    panel.append(optRow, msg, list);
    // Show right after "My pickups" so a request is hard to miss.
    const picks = [...root.querySelectorAll(".panel")].find((p) => p.querySelector("h2")?.textContent === "My pickups");
    if (picks) picks.after(panel); else root.append(panel);

    let active = false;
    function drawToggle() {
      toggle.textContent = active ? "✔ I do backup collections (turn off)" : "Offer backup collections";
      toggle.className = active ? "on-green" : "primary";
      toggle.style.cssText = "width:auto;margin-top:0";
    }

    async function save(nextActive) {
      try {
        const r = await ui.api("/backup", { method: "PUT", body: JSON.stringify({ active: nextActive, max_distance_km: Number(dist.value) }) });
        active = r.volunteer.active;
        drawToggle();
        msg.textContent = active ? "You will receive backup requests." : "Backup collections turned off.";
        msg.className = "msg ok";
      } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
    }
    toggle.addEventListener("click", () => save(!active));
    dist.addEventListener("change", () => { if (active) save(true); });

    async function loadRequests() {
      const reqs = await ui.api("/backup-requests");
      list.replaceChildren();
      for (const r of reqs) {
        const l = r.listing;
        const card = ui.el("div", { class: "listing" });
        card.append(ui.el("img", { src: `/api/listings/${l.id}/photo`, alt: l.food_type }));
        const b = ui.el("div");
        const h = ui.el("h3");
        h.append(ui.el("span", { class: "pill new" }, "BACKUP REQUEST"), `${l.food_type} — ${l.quantity} ${l.unit}`);
        b.append(h, ui.el("div", { class: "meta" },
          `${l.restaurant.name}, ${l.restaurant.address} · ~${Math.round(r.eta_minutes)} min away · best before ${fmt(l.expires_at)}`));
        b.append(ui.el("div", { class: "meta" }, "Collect it and store it at your NGO. Please answer within 10 minutes."));
        const row = ui.el("div", { class: "row" });
        const yes = ui.el("button", { type: "button", class: "primary", style: "width:auto;margin-top:0" }, "Accept backup collection");
        const no = ui.el("button", { type: "button" }, "Decline");
        const m = ui.el("div", { class: "msg" });
        const act = (action) => async () => {
          yes.disabled = no.disabled = true;
          try {
            await ui.api(`/backup-requests/${r.id}/${action}`, { method: "POST" });
            if (window.FRSFeedReload) await window.FRSFeedReload();
            await loadRequests();
          } catch (e) { m.textContent = e.message; m.className = "msg err"; yes.disabled = no.disabled = false; }
        };
        yes.addEventListener("click", act("accept"));
        no.addEventListener("click", act("decline"));
        row.append(yes, no);
        b.append(row, m);
        card.append(b);
        list.append(card);
      }
    }

    ui.api("/backup").then((r) => {
      active = !!(r.volunteer && r.volunteer.active);
      if (r.volunteer) dist.value = String(r.volunteer.max_distance_km);
      drawToggle();
    }).catch(() => drawToggle());
    loadRequests().catch(() => {});
    clearInterval(timer);
    timer = setInterval(() => loadRequests().catch(() => {}), 15000);
  });
})();
