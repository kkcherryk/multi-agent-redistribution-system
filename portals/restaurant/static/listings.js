// Restaurant portal, Phase 3: post surplus food and track listings (Module 6).
(function () {
  window.PORTAL.extras = window.PORTAL.extras || [];
  let timer = null;

  function localInput(date) {
    const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return d.toISOString().slice(0, 16);
  }
  const fmt = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const LABEL = { veg: "Veg", non_veg: "Non-veg", mixed: "Mixed" };

  window.PORTAL.extras.push((acc, ui) => {
    const root = document.getElementById("food");
    root.replaceChildren();
    if (acc.status !== "verified") return;  // only verified restaurants may post (Module 6)

    // ---- post form
    const panel = ui.el("div", { class: "panel" });
    panel.append(ui.el("h2", {}, "Post surplus food"));
    const f = ui.el("form", { class: "grid2" });
    const field = (label, input, wide) => {
      const w = ui.el("div", wide ? { style: "grid-column:1/-1" } : {});
      w.append(ui.el("label", { for: input.id }, label), input);
      f.append(w);
      return input;
    };
    const select = (id, opts) => {
      const s = ui.el("select", { id, name: id });
      for (const [v, t] of opts) s.append(ui.el("option", { value: v }, t));
      return s;
    };
    field("Food (e.g. Veg biryani)", ui.el("input", { id: "food_type", name: "food_type", required: "", maxlength: "120" }), true);
    field("Quantity", ui.el("input", { id: "quantity", name: "quantity", type: "number", min: "0.1", step: "0.1", required: "" }));
    field("Unit", select("unit", [["portions", "Portions"], ["kg", "Kilograms"]]));
    field("Type", select("dietary", [["veg", "Veg"], ["non_veg", "Non-veg"], ["mixed", "Mixed"]]));
    field("Condition", select("condition", [["fresh", "Fresh"], ["good", "Good"], ["fair", "Fair"]]));
    const prep = field("Prepared at", ui.el("input", { id: "prepared_at", name: "prepared_at", type: "datetime-local", required: "" }));
    const exp = field("Best before (expiry)", ui.el("input", { id: "expires_at", name: "expires_at", type: "datetime-local", required: "" }));
    prep.value = localInput(new Date());
    exp.value = localInput(new Date(Date.now() + 4 * 3600 * 1000));
    field("Description (optional)", ui.el("textarea", { id: "description", name: "description", rows: "2", maxlength: "1000" }), true);
    field("Pickup notes (optional)", ui.el("input", { id: "pickup_notes", name: "pickup_notes", maxlength: "500", placeholder: "e.g. ask for Ramesh at the back door" }), true);
    field("Food photo", ui.el("input", { id: "photo", name: "photo", type: "file", accept: "image/jpeg,image/png,image/webp", required: "" }), true);
    const btn = ui.el("button", { type: "submit", class: "primary", style: "grid-column:1/-1" }, "Post food");
    const msg = ui.el("div", { class: "msg", style: "grid-column:1/-1" });
    f.append(btn, msg);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = {
        food_type: f.food_type.value, quantity: Number(f.quantity.value), unit: f.unit.value,
        dietary: f.dietary.value, condition: f.condition.value,
        prepared_at: new Date(f.prepared_at.value).toISOString(), expires_at: new Date(f.expires_at.value).toISOString(),
        description: f.description.value || null, pickup_notes: f.pickup_notes.value || null,
      };
      const fd = new FormData();
      fd.append("data", JSON.stringify(data));
      fd.append("photo", f.photo.files[0]);
      btn.disabled = true;
      try {
        await ui.api("/listings", { method: "POST", body: fd });
        f.food_type.value = f.quantity.value = f.description.value = f.pickup_notes.value = "";
        f.photo.value = "";
        msg.textContent = "Posted. Nearby NGO agents have been alerted.";
        msg.className = "msg ok";
        await loadList();
      } catch (err) { msg.textContent = err.message; msg.className = "msg err"; }
      btn.disabled = false;
    });
    panel.append(f);

    // ---- my listings
    const listPanel = ui.el("div", { class: "panel" });
    listPanel.append(ui.el("h2", {}, "My food listings"));
    const list = ui.el("div");
    listPanel.append(list);
    root.append(panel, listPanel);

    async function loadList() {
      const items = await ui.api("/listings");
      list.replaceChildren();
      if (!items.length) list.append(ui.el("p", { class: "note" }, "You haven't posted any food yet."));
      for (const it of items) {
        const card = ui.el("div", { class: "listing" });
        card.append(ui.el("img", { src: `/api/listings/${it.id}/photo`, alt: it.food_type }));
        const body = ui.el("div");
        const h = ui.el("h3", {}, `${it.food_type} — ${it.quantity} ${it.unit}`);
        body.append(h, ui.statusChip(it.status));
        body.append(ui.el("div", { class: "meta" },
          `${LABEL[it.dietary]} · ${it.condition} · prepared ${fmt(it.prepared_at)} · best before ${fmt(it.expires_at)}`));
        if (it.assignment) {
          const a = it.assignment;
          body.append(ui.el("div", { class: "callout ok" },
            `Collector: ${a.representative_name} (${a.ngo_name})${a.phone ? " · " + a.phone : ""} — accepted ${fmt(a.accepted_at)}`));
        }
        if (it.inspection) {
          const i = it.inspection;
          body.append(ui.el("div", { class: "meta" }, i.outcome === "rejected"
            ? `Rejected at pickup: ${i.notes}`
            : `Collected ${i.actual_quantity} of ${i.listed_quantity} ${it.unit} (${i.observed_condition})${i.notes ? " — " + i.notes : ""}`));
        }
        for (const h of it.handovers || []) {
          const where = { delivered: `Delivered to ${h.recipient ? h.recipient.name : "recipient"}`,
                          stored: "Stored at the NGO for later", distributed: `Given from NGO storage to ${h.recipient ? h.recipient.name : "recipient"}`,
                          disposed: "Disposed by the NGO" }[h.kind];
          body.append(ui.el("div", { class: "meta" }, `${where} — ${h.quantity} ${it.unit}, ${fmt(h.created_at)}`));
        }
        if (it.status === "active") {
          const c = ui.el("button", { type: "button", class: "small danger" }, "Cancel listing");
          c.addEventListener("click", async () => {
            if (!confirm("Cancel this food listing?")) return;
            try { await ui.api(`/listings/${it.id}/cancel`, { method: "POST" }); await loadList(); }
            catch (err) { alert(err.message); }
          });
          body.append(c);
        }
        card.append(body);
        list.append(card);
      }
    }
    loadList().catch((e) => list.replaceChildren(ui.el("p", { class: "msg err" }, e.message)));
    clearInterval(timer);
    timer = setInterval(() => loadList().catch(() => {}), 20000);  // pick up "NGO assigned" without reloading
  });
})();
