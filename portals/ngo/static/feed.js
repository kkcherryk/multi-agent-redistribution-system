// NGO portal, Phase 3: nearby food alerts, accept, and my pickups (Module 7, ride-hailing style).
(function () {
  window.PORTAL.extras = window.PORTAL.extras || [];
  let timer = null;
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const LABEL = { veg: "Veg", non_veg: "Non-veg", mixed: "Mixed" };

  function timeLeft(iso) {
    const mins = Math.round((new Date(iso) - Date.now()) / 60000);
    if (mins <= 0) return "expiring now";
    return mins < 60 ? `${mins} min left` : `${Math.floor(mins / 60)} h ${mins % 60} min left`;
  }

  function mapLink(ui, lat, lon, text) {
    return ui.el("a", { href: `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`,
                        target: "_blank", rel: "noopener" }, text);
  }

  window.PORTAL.extras.push((acc, ui) => {
    const root = document.getElementById("food");
    root.replaceChildren();
    if (acc.status !== "verified") return;  // only verified NGO agents see food (Module 7)

    const pickPanel = ui.el("div", { class: "panel" });
    pickPanel.append(ui.el("h2", {}, "My pickups"));
    const picks = ui.el("div");
    pickPanel.append(picks);

    const feedPanel = ui.el("div", { class: "panel" });
    const title = ui.el("h2", {}, "Nearby food");
    const sub = ui.el("p", { class: "note" });
    const feed = ui.el("div");
    feedPanel.append(title, sub, feed);
    root.append(pickPanel, feedPanel);

    async function load() {
      const [data, mine] = await Promise.all([ui.api("/feed"), ui.api("/pickups")]);
      const available = acc.badges && acc.badges.availability && acc.badges.availability.label === "Available";
      const busy = mine.some((m) => m.status === "assigned");  // one pickup at a time
      sub.textContent = `Food within ${data.radius_km} km of your NGO, nearest first. ` +
        (!available ? "You are Unavailable — switch to Available below to accept pickups."
          : busy ? "Finish your current pickup before accepting another one."
          : "Updates every 15 seconds.");

      picks.replaceChildren();
      if (!mine.length) picks.append(ui.el("p", { class: "note" }, "No pickups yet. Accept food below."));
      for (const it of mine) {
        const card = ui.el("div", { class: "listing" });
        card.append(ui.el("img", { src: `/api/listings/${it.id}/photo`, alt: it.food_type }));
        const b = ui.el("div");
        b.append(ui.el("h3", {}, `${it.food_type} — ${it.quantity} ${it.unit}`), ui.statusChip(it.status));
        const r = it.restaurant;
        const meta = ui.el("div", { class: "meta" });
        meta.append(`Collect from ${r.name}, ${r.address} (`, mapLink(ui, it.latitude, it.longitude, "map"),
                    `)${r.contact_name ? " · contact " + r.contact_name : ""}${r.phone ? " · " + r.phone : ""}`);
        b.append(meta, ui.el("div", { class: "meta" }, `Best before ${fmt(it.expires_at)}${it.pickup_notes ? " · " + it.pickup_notes : ""}`));
        card.append(b);
        picks.append(card);
      }

      const fresh = data.listings.filter((x) => x.new).length;
      title.textContent = "Nearby food" + (fresh ? ` (${fresh} new)` : "");
      feed.replaceChildren();
      if (!data.listings.length) feed.append(ui.el("p", { class: "note" }, "No food waiting nearby right now."));
      for (const it of data.listings) {
        const card = ui.el("div", { class: "listing" });
        card.append(ui.el("img", { src: `/api/listings/${it.id}/photo`, alt: it.food_type }));
        const b = ui.el("div");
        const h = ui.el("h3");
        if (it.new) h.append(ui.el("span", { class: "pill new" }, "NEW"));
        h.append(`${it.food_type} — ${it.quantity} ${it.unit}`);
        b.append(h);
        b.append(ui.el("span", { class: "pill " + it.dietary }, LABEL[it.dietary]), ui.el("span", { class: "pill" }, it.condition),
                 ui.el("span", { class: "pill" }, `${it.distance_km} km`), ui.el("span", { class: "pill" }, timeLeft(it.expires_at)));
        const meta = ui.el("div", { class: "meta" });
        meta.append(`${it.restaurant.name}, ${it.restaurant.address} (`, mapLink(ui, it.latitude, it.longitude, "map"), ")");
        b.append(meta);
        if (it.description) b.append(ui.el("div", { class: "meta" }, it.description));
        const btn = ui.el("button", { type: "button", class: "primary", style: "width:auto" }, "Accept pickup");
        btn.disabled = !available || busy;
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          try { await ui.api(`/listings/${it.id}/accept`, { method: "POST" }); }
          catch (err) { alert(err.message); }  // e.g. another agent accepted first
          await load().catch(() => {});
        });
        b.append(btn);
        card.append(b);
        feed.append(card);
      }
    }
    load().catch((e) => feed.replaceChildren(ui.el("p", { class: "msg err" }, e.message)));
    clearInterval(timer);
    timer = setInterval(() => load().catch(() => {}), 15000);
  });
})();
