// NGO portal, Phase 4: pickup steps, food check, delivery, storage and recipients (Modules 8 and 9).
(function () {
  const KIND = { orphanage: "Orphanage", shelter: "Shelter / homeless", old_age_home: "Old-age home",
                 community_kitchen: "Community kitchen", slum_community: "Slum community", other: "Other" };
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  let recipients = [];

  let uid = 0;  // label <-> control association, so labels work for screen readers and clicks
  function input(ui, attrs, label) {
    const id = "pk-" + (++uid);
    const w = ui.el("div");
    const own = Object.fromEntries(Object.entries(attrs).filter(([k]) => k !== "tag"));
    const i = ui.el(attrs.tag || "input", { ...own, id });
    w.append(ui.el("label", { for: id }, label), i);
    return [w, i];
  }

  function select(ui, options, label) {
    const id = "pk-" + (++uid);
    const s = ui.el("select", { id });
    for (const [v, t] of options) s.append(ui.el("option", { value: v }, t));
    const w = ui.el("div");
    w.append(ui.el("label", { for: id }, label), s);
    return [w, s];
  }

  async function post(ui, path, body) {
    return ui.api(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function summary(ui, it) {
    const box = ui.el("div", { class: "meta" });
    if (it.inspection) {
      const i = it.inspection;
      box.append(ui.el("div", {}, `Food check: ${i.outcome} — ${i.actual_quantity} of ${i.listed_quantity} ${it.unit}, ${i.observed_condition}` +
        (i.notes ? ` (${i.notes})` : "")));
    }
    for (const h of it.handovers || []) {
      const to = h.recipient ? `${h.recipient.name}` : "";
      const text = { delivered: `Delivered ${h.quantity} ${it.unit} to ${to}, received by ${h.received_by}`,
                     stored: `Stored ${h.quantity} ${it.unit} at the NGO`,
                     distributed: `Gave ${h.quantity} ${it.unit} from storage to ${to}, received by ${h.received_by}`,
                     disposed: `Disposed: ${h.notes}` }[h.kind];
      box.append(ui.el("div", {}, `${fmt(h.created_at)} — ${text}`));
    }
    return box;
  }

  // Renders the action area for one pickup according to its assignment status.
  function actions(ui, it, reload) {
    const wrap = ui.el("div", { style: "margin-top:8px" });
    const msg = ui.el("div", { class: "msg" });
    const run = (fn) => async () => {
      try { await fn(); await reload(); } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
    };
    const status = it.assignment ? it.assignment.status : null;
    const base = `/pickups/${it.id}`;

    if (status === "accepted" || status === "en_route") {
      const row = ui.el("div", { class: "row" });
      if (status === "accepted") {
        const go = ui.el("button", { type: "button", class: "primary", style: "width:auto;margin-top:0" }, "I'm on my way");
        go.addEventListener("click", run(() => post(ui, `${base}/start`)));
        row.append(go);
      }
      const back = ui.el("button", { type: "button", class: "small danger" }, "Give back pickup");
      back.addEventListener("click", run(async () => {
        const reason = prompt("Why are you giving this pickup back? (optional)") ;
        if (reason === null) throw new Error("Cancelled.");
        return post(ui, `${base}/release`, { reason });
      }));
      row.append(back);
      wrap.append(row);

      // Food check at the restaurant (Module 8)
      const form = ui.el("div", { class: "card", style: "margin-top:10px" });
      form.append(ui.el("strong", {}, "Food check at the restaurant"));
      const g = ui.el("div", { class: "grid2" });
      const [qw, qty] = input(ui, { type: "number", min: "0", step: "0.1", value: String(it.quantity) }, `Quantity you received (${it.unit})`);
      const [cw, cond] = select(ui, [["good", "Good"], ["fresh", "Fresh"], ["fair", "Fair"], ["spoiled", "Spoiled"]], "Condition you observed");
      const [nw, notes] = input(ui, { tag: "textarea", rows: "2", maxlength: "1000" }, "Notes (needed if quantity is less, or to reject)");
      nw.style.gridColumn = "1/-1";
      g.append(qw, cw, nw);
      const r2 = ui.el("div", { class: "row" });
      const ok = ui.el("button", { type: "button", class: "primary", style: "width:auto;margin-top:0" }, "Food checked — collect");
      const no = ui.el("button", { type: "button", class: "danger" }, "Reject food");
      ok.addEventListener("click", run(() => post(ui, `${base}/inspection`, { decision: "collect", actual_quantity: Number(qty.value), observed_condition: cond.value, notes: notes.value || null })));
      no.addEventListener("click", run(() => post(ui, `${base}/inspection`, { decision: "reject", actual_quantity: Number(qty.value) || 0, observed_condition: cond.value, notes: notes.value || null })));
      r2.append(ok, no);
      form.append(g, r2);
      wrap.append(form);
    } else if (status === "collected") {
      const form = ui.el("div", { class: "card" });
      form.append(ui.el("strong", {}, "Hand over the food"));
      if (!recipients.length) form.append(ui.el("p", { class: "note" }, "Add a recipient below (orphanage, shelter, old-age home…) to deliver food, or store it at the NGO."));
      const g = ui.el("div", { class: "grid2" });
      const [rw, rec] = select(ui, recipients.map((r) => [r.id, `${r.name} (${KIND[r.kind]})`]), "Recipient");
      const [bw, by] = input(ui, { maxlength: "120", placeholder: "Name of the person who received it" }, "Received by");
      g.append(rw, bw);
      const deliver = ui.el("button", { type: "button", class: "primary", style: "width:auto;margin-top:8px" }, "Confirm delivery");
      deliver.disabled = !recipients.length;
      deliver.addEventListener("click", run(() => post(ui, `${base}/deliver`, { recipient_id: rec.value, received_by: by.value })));
      const row = ui.el("div", { class: "row", style: "margin-top:8px" });
      const store = ui.el("button", { type: "button" }, "Store at NGO for later");
      store.addEventListener("click", run(() => post(ui, `${base}/store`, {})));
      const dispose = ui.el("button", { type: "button", class: "small danger" }, "Record as disposed");
      dispose.addEventListener("click", run(async () => {
        const reason = prompt("Why was this food disposed?");
        if (!reason) throw new Error("A reason is needed to record disposal.");
        return post(ui, `${base}/dispose`, { reason });
      }));
      row.append(store, dispose);
      form.append(g, deliver, row);
      wrap.append(form);
    }
    wrap.append(msg);
    return wrap;
  }

  // Public API used by feed.js to draw "My pickups".
  window.FRSPickups = {
    busyStatuses: ["assigned", "collected"],
    async refreshRecipients(ui) { recipients = await ui.api("/recipients"); return recipients; },
    render(ui, container, items, reload, mapLink) {
      container.replaceChildren();
      const current = items.filter((x) => ["assigned", "collected"].includes(x.status));
      const past = items.filter((x) => !["assigned", "collected"].includes(x.status));
      if (!current.length) container.append(ui.el("p", { class: "note" }, "No pickup in progress. Accept food below."));
      for (const it of current) container.append(card(ui, it, reload, mapLink, true));
      if (past.length) {
        const d = ui.el("details");
        d.append(ui.el("summary", {}, `Past pickups (${past.length})`));
        for (const it of past) d.append(card(ui, it, reload, mapLink, false));
        container.append(d);
      }
    },
  };

  function card(ui, it, reload, mapLink, live) {
    const c = ui.el("div", { class: "listing" });
    c.append(ui.el("img", { src: `/api/listings/${it.id}/photo`, alt: it.food_type }));
    const b = ui.el("div");
    const step = it.assignment && it.assignment.status === "en_route" ? "On the way" : null;
    b.append(ui.el("h3", {}, `${it.food_type} — ${it.quantity} ${it.unit}`), ui.statusChip(it.status));
    if (step) b.append(ui.el("span", { class: "pill" }, step));
    const r = it.restaurant;
    const meta = ui.el("div", { class: "meta" });
    meta.append(`${r.name}, ${r.address} (`, mapLink(ui, it.latitude, it.longitude, "map"),
                `)${r.contact_name ? " · contact " + r.contact_name : ""}${r.phone ? " · " + r.phone : ""}`);
    b.append(meta, ui.el("div", { class: "meta" }, `Best before ${fmt(it.expires_at)}${it.pickup_notes ? " · " + it.pickup_notes : ""}`));
    b.append(summary(ui, it));
    if (live) {
      const routeBox = ui.el("div", { class: "card", style: "margin-top:8px" });
      routeBox.append(ui.el("span", { class: "note" }, "Planning route…"));
      b.append(routeBox, actions(ui, it, reload));
      ui.api(`/pickups/${it.id}/route`).then((r) => {
        routeBox.replaceChildren(ui.el("strong", {}, r.backup_collection ? "Backup collection route (store at your NGO)" : "Suggested route"));
        routeBox.append(ui.el("div", { class: "meta" },
          `${r.order.join(" → ")} · about ${r.total_km} km, ${Math.round(r.total_minutes)} min`));
        for (const l of r.legs) routeBox.append(ui.el("div", { class: "meta" }, `${l.from} → ${l.to}: ${l.km} km, ~${Math.round(l.minutes)} min`));
        routeBox.append(ui.el("a", { href: r.directions_url, target: "_blank", rel: "noopener" }, "Open turn-by-turn directions"));
        routeBox.append(ui.el("div", { class: "note" }, "Estimate: " + r.assumptions));
      }).catch((e) => routeBox.replaceChildren(ui.el("span", { class: "note" }, "Route unavailable: " + e.message)));
    }
    c.append(b);
    return c;
  }

  // ---- Recipients and storage panels (registered after the feed)
  window.PORTAL.extras = window.PORTAL.extras || [];
  window.PORTAL.extras.push((acc, ui) => {
    if (acc.status !== "verified") return;
    const root = document.getElementById("food");

    const stockPanel = ui.el("div", { class: "panel" });
    stockPanel.append(ui.el("h2", {}, "Stored food"));
    const stock = ui.el("div");
    stockPanel.append(stock);

    const recPanel = ui.el("div", { class: "panel" });
    recPanel.append(ui.el("h2", {}, "Recipients"));
    const recList = ui.el("div");
    recPanel.append(ui.el("p", { class: "note" }, "Places you deliver food to: orphanages, shelters, old-age homes and so on."), recList);
    const f = ui.el("form", { class: "grid2" });
    const [n1, name] = input(ui, { required: "", maxlength: "120" }, "Name");
    const [k1, kind] = select(ui, Object.entries(KIND), "Type");
    const [a1, address] = input(ui, { required: "", maxlength: "300" }, "Address");
    const [p1, phone] = input(ui, { maxlength: "20" }, "Contact phone (optional)");
    const [la1, rlat] = input(ui, { type: "number", step: "any", min: "-90", max: "90" }, "Latitude (for route planning)");
    const [lo1, rlon] = input(ui, { type: "number", step: "any", min: "-180", max: "180" }, "Longitude");
    a1.style.gridColumn = "1/-1";
    const add = ui.el("button", { type: "submit", class: "primary", style: "grid-column:1/-1" }, "Add recipient");
    const fmsg = ui.el("div", { class: "msg", style: "grid-column:1/-1" });
    f.append(n1, k1, a1, p1, la1, lo1, add, fmsg);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await ui.api("/recipients", { method: "POST", body: JSON.stringify({ name: name.value, kind: kind.value, address: address.value, contact_phone: phone.value || null,
          latitude: rlat.value === "" ? null : Number(rlat.value), longitude: rlon.value === "" ? null : Number(rlon.value) }) });
        f.reset();
        fmsg.textContent = "Recipient added.";
        fmsg.className = "msg ok";
        await window.FRSFeedReload();
      } catch (err) { fmsg.textContent = err.message; fmsg.className = "msg err"; }
    });
    recPanel.append(f);
    root.append(stockPanel, recPanel);

    window.FRSStorageRender = (items) => {
      recList.replaceChildren();
      for (const r of recipients) recList.append(ui.el("div", { class: "doc" }, `${r.name} — ${KIND[r.kind]} — ${r.address}`));
      if (!recipients.length) recList.append(ui.el("p", { class: "note" }, "No recipients yet."));

      stock.replaceChildren();
      const live = items.filter((x) => x.status === "in_stock");
      if (!live.length) stock.append(ui.el("p", { class: "note" }, "Nothing in storage."));
      for (const s of live) {
        const c = ui.el("div", { class: "card" });
        c.append(ui.el("strong", {}, `${s.food_type} — ${s.remaining_quantity} of ${s.stored_quantity} ${s.unit} left`));
        c.append(ui.el("div", { class: "meta" }, `At ${s.location} · best before ${fmt(s.expires_at)}`));
        const msg = ui.el("div", { class: "msg" });
        if (s.expired) {
          c.append(ui.el("div", { class: "callout err" }, "Past best-before time — do not distribute. Record it as disposed."));
        } else {
          const g = ui.el("div", { class: "grid2" });
          const [rw, rec] = select(ui, recipients.map((r) => [r.id, r.name]), "Give to");
          const [qw, qty] = input(ui, { type: "number", min: "0.1", step: "0.1", value: String(s.remaining_quantity) }, `Quantity (${s.unit})`);
          const [bw, by] = input(ui, { maxlength: "120" }, "Received by");
          g.append(rw, qw, bw);
          const give = ui.el("button", { type: "button", class: "small" }, "Give out");
          give.disabled = !recipients.length;
          give.addEventListener("click", async () => {
            try {
              await ui.api(`/storage/${s.id}/distribute`, { method: "POST", body: JSON.stringify({ recipient_id: rec.value, quantity: Number(qty.value), received_by: by.value }) });
              await window.FRSFeedReload();
            } catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
          });
          c.append(g, give);
        }
        const dis = ui.el("button", { type: "button", class: "small danger" }, "Dispose");
        dis.addEventListener("click", async () => {
          const reason = prompt("Why is this stock being disposed?");
          if (!reason) return;
          try { await ui.api(`/storage/${s.id}/dispose`, { method: "POST", body: JSON.stringify({ reason }) }); await window.FRSFeedReload(); }
          catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
        });
        c.append(dis, msg);
        stock.append(c);
      }
    };
  });
})();
