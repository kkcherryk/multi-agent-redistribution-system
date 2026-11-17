// Onboarding UI for the applicant portals (Restaurant, NGO): profile, evidence uploads, submit, availability.
(function () {
  const FIELDS = {
    restaurant: [
      ["restaurant_name", "Restaurant name"], ["applicant_name", "Applicant (your) name"],
      ["licence_number", "Food licence number (e.g. FSSAI)"], ["address", "Address", "wide"],
      ["latitude", "Latitude", "num"], ["longitude", "Longitude", "num"],
      ["additional_info", "Additional information (optional)", "area"],
    ],
    ngo: [
      ["ngo_name", "NGO name"], ["ngo_registration_id", "NGO registration ID"],
      ["representative_name", "Representative (collecting agent) name"], ["address", "NGO address", "wide"],
      ["latitude", "Latitude", "num"], ["longitude", "Longitude", "num"],
      ["additional_info", "Additional information (optional)", "area"],
    ],
  };
  const STATUS_TEXT = {
    applicant: "Complete the steps below and submit your application for host review.",
    under_review: "Your application is with the host for review. You can't edit it while it is being reviewed.",
    on_hold: "The host needs more information. Update your details or documents below and submit again.",
    verified: "Your account is verified.",
    declined: "Your application was declined. Contact the project team if you think this is a mistake.",
  };

  let ui, root, role;

  function section(title) {
    const s = ui.el("div", { class: "panel" });
    s.append(ui.el("h2", {}, title));
    root.append(s);
    return s;
  }

  function fail(where, err) {
    where.textContent = err.message;
    where.className = "msg err";
  }

  // ---- location picker: a map pin instead of typed latitude/longitude, plus address and nearby landmark.
  // The landmark travels inside the address text ("<address> · Near: <landmark>"), so the core API is unchanged.
  const NEAR = " · Near: ";
  const DEFAULT_CENTER = [12.9716, 77.5946];  // Bengaluru

  async function lookupAddress(lat, lon) {
    // OpenStreetMap's Nominatim; it needs a Referer, and the page policy is same-origin, so send our origin.
    const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=18&addressdetails=1&lat=${lat}&lon=${lon}`;
    const res = await fetch(url, { referrerPolicy: "strict-origin-when-cross-origin", headers: { "Accept-Language": "en" } });
    if (!res.ok) throw new Error("lookup failed");
    const d = await res.json();
    const a = d.address || {};
    const parts = [a.house_number && a.road ? `${a.house_number}, ${a.road}` : a.road || a.building || a.amenity,
      a.neighbourhood || a.quarter, a.suburb, a.city || a.town || a.village, a.postcode];
    const seen = new Set();
    const text = parts.filter((p) => p && !seen.has(p) && seen.add(p)).join(", ");
    return text || (d.display_name || "").replace(/, India$/, "");
  }

  function locationPicker(form, label, profile, editable, msg) {
    const box = ui.el("div", { class: "loc-picker", style: "grid-column:1/-1" });
    box.append(ui.el("label", {}, "Location on the map"));
    const hint = ui.el("p", { class: "note loc-hint" },
      editable ? "Tap the map or drag the pin to your exact entrance, or use your current location." : "Your saved location.");
    const mapEl = ui.el("div", { class: "loc-map" });
    const locate = ui.el("button", { type: "button", class: "loc-btn" }, "📍 Use my current location");
    const status = ui.el("span", { class: "note loc-status" });
    const row = ui.el("div", { class: "row" });
    row.append(locate, status);
    box.append(hint, mapEl, row);

    const [addr0, near0] = ((profile && profile.address) || "").split(NEAR);
    const addrWrap = ui.el("div", { style: "grid-column:1/-1" });
    addrWrap.append(ui.el("label", { for: "pf-address" }, label));
    const addr = ui.el("textarea", { id: "pf-address", name: "address", rows: "2", maxlength: "300",
      placeholder: "Filled in from the pin — you can edit it" });
    addr.value = addr0 || "";
    addrWrap.append(addr);
    const nearWrap = ui.el("div", { style: "grid-column:1/-1" });
    nearWrap.append(ui.el("label", { for: "pf-landmark" }, "Nearby landmark (optional)"));
    const near = ui.el("input", { id: "pf-landmark", maxlength: "120", placeholder: "e.g. Opposite City Mall, next to the bus stop" });
    near.value = near0 || "";
    nearWrap.append(near);
    form.append(box, addrWrap, nearWrap);
    addr.disabled = near.disabled = locate.disabled = !editable;
    if (!editable) locate.classList.add("hidden");

    let pos = profile && profile.latitude != null ? [profile.latitude, profile.longitude] : null;
    let map = null, marker = null, lookupTimer = null, lookupSeq = 0;
    const setStatus = (t) => { status.textContent = t; };

    function place(latlng, fromUser) {
      pos = [Number(latlng[0].toFixed(6)), Number(latlng[1].toFixed(6))];
      if (!map) return;
      if (!marker) {
        marker = L.marker(pos, { draggable: editable, autoPan: true }).addTo(map);
        marker.on("dragend", () => { const p = marker.getLatLng(); place([p.lat, p.lng], true); });
      } else marker.setLatLng(pos);
      if (!fromUser) return;
      clearTimeout(lookupTimer);
      const seq = ++lookupSeq;
      setStatus("Finding the address…");
      lookupTimer = setTimeout(async () => {
        try {
          const text = await lookupAddress(pos[0], pos[1]);
          if (seq !== lookupSeq) return;
          if (text) { addr.value = text; setStatus("Address filled in — check it and add a landmark if it helps."); }
          else setStatus("Couldn't find an address here — please type it.");
        } catch { if (seq === lookupSeq) setStatus("Couldn't look up the address — please type it."); }
      }, 700);  // wait until the pin settles; Nominatim allows about one lookup a second
    }

    if (!window.L) {
      mapEl.replaceWith(ui.el("p", { class: "msg err" }, "The map could not load (no internet connection?). Your current location can still be used."));
    } else {
      map = L.map(mapEl, { scrollWheelZoom: false }).setView(pos || DEFAULT_CENTER, pos ? 16 : 12);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, referrerPolicy: "strict-origin-when-cross-origin",
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
      if (pos) place(pos, false);
      if (editable) map.on("click", (e) => place([e.latlng.lat, e.latlng.lng], true));
      setTimeout(() => map.invalidateSize(), 50);  // the container was sized after the map was created
    }

    locate.addEventListener("click", () => {
      if (!navigator.geolocation) return fail(msg, new Error("Location is not available in this browser."));
      setStatus("Getting your location…");
      navigator.geolocation.getCurrentPosition(
        (p) => {
          const ll = [p.coords.latitude, p.coords.longitude];
          if (map) map.setView(ll, 17);
          place(ll, true);
          if (!map) setStatus("Location saved from your device — please type the address.");
        },
        () => { setStatus(""); fail(msg, new Error("Could not read your location. Tap your place on the map instead.")); },
        { enableHighAccuracy: true, timeout: 15000 });
    });

    return {
      value() {
        const a = addr.value.trim(), n = near.value.trim();
        return { pinned: !!pos, latitude: pos && pos[0], longitude: pos && pos[1], address: n ? a + NEAR + n : a };
      },
    };
  }

  async function render() {
    const ob = await ui.api("/onboarding");
    root.replaceChildren();
    const editable = ob.editable;

    // ---- status + checklist
    const st = section("Verification");
    st.append(ui.el("p", {}, STATUS_TEXT[ob.status] || ""));
    const latest = ob.reviews[0];
    if (latest && latest.note && ["on_hold", "declined"].includes(ob.status)) {
      const c = ui.el("div", { class: "callout" + (ob.status === "declined" ? " err" : "") });
      c.append(ui.el("strong", {}, "Message from the host: "), document.createTextNode(latest.note));
      st.append(c);
    }
    if (editable) {
      if (ob.missing.length) {
        st.append(ui.el("p", { class: "note" }, "Still needed before you can submit:"));
        const ul = ui.el("ul", { class: "checklist" });
        ob.missing.forEach((m) => ul.append(ui.el("li", {}, m)));
        st.append(ul);
      }
      st.append(ui.el("p", { class: "note" }, "Fill in your details and upload the documents below, then submit at the bottom of the page."));
    }

    // ---- availability (NGO agents only, Module 3/5)
    if (role === "ngo") {
      const av = section("Availability");
      const available = ob.badges.availability && ob.badges.availability.label === "Available";
      av.append(ui.el("p", { class: "note" },
        "Tell the host and the matching system whether you can collect food right now. Availability is separate from verification."));
      const t = ui.el("div", { class: "toggle" });
      const on = ui.el("button", { type: "button", class: available ? "on-green" : "" }, "● Available");
      const off = ui.el("button", { type: "button", class: available ? "" : "on-red" }, "● Unavailable");
      const msg = ui.el("div", { class: "msg" });
      if (!ob.profile) { on.disabled = off.disabled = true; msg.textContent = "Save your NGO profile first."; msg.className = "msg"; }
      const set = (value) => async () => {
        try { await ui.api("/availability", { method: "PUT", body: JSON.stringify({ available: value }) }); await refresh(); }
        catch (e) { fail(msg, e); }
      };
      on.addEventListener("click", set(true));
      off.addEventListener("click", set(false));
      t.append(on, off);
      av.append(t, msg);
    }

    // ---- profile form
    const pf = section(role === "ngo" ? "NGO and representative details" : "Restaurant details");
    const form = ui.el("form", { class: "grid2" });
    const msg = ui.el("div", { class: "msg", style: "grid-column:1/-1" });
    let picker = null;
    for (const [name, label, kind] of FIELDS[role]) {
      if (kind === "num") continue;  // latitude/longitude come from the map pin
      if (name === "address") { picker = locationPicker(form, label, ob.profile, editable, msg); continue; }
      const wrap = ui.el("div", kind === "wide" || kind === "area" ? { style: "grid-column:1/-1" } : {});
      const id = "pf-" + name;
      wrap.append(ui.el("label", { for: id }, label));
      const input = kind === "area" ? ui.el("textarea", { id, name, rows: "3", maxlength: "2000" })
        : ui.el("input", kind === "num" ? { id, name, type: "number", step: "any", required: "" } : { id, name, required: "", maxlength: "300" });
      if (kind === "area") input.removeAttribute("required");
      const v = ob.profile ? ob.profile[name] : null;
      if (v !== null && v !== undefined) input.value = v;
      input.disabled = !editable;
      wrap.append(input);
      form.append(wrap);
    }
    const actions = ui.el("div", { class: "row", style: "grid-column:1/-1;margin-top:12px" });
    const save = ui.el("button", { type: "submit", class: "primary", style: "width:auto;margin-top:0" }, "Save details");
    save.disabled = !editable;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const loc = picker.value();
      if (!loc.pinned) return fail(msg, new Error("Mark your location on the map (tap the map or use your current location)."));
      if (!loc.address) return fail(msg, new Error("Enter your address."));
      if (loc.address.length > 300) return fail(msg, new Error("Address and landmark together must be 300 characters or fewer."));
      const body = {};
      for (const [name, , kind] of FIELDS[role]) {
        if (kind === "num") body[name] = loc[name];
        else if (name === "address") body[name] = loc.address;
        else body[name] = form[name].value;
      }
      try {
        await ui.api("/profile", { method: "PUT", body: JSON.stringify(body) });
        await refresh();
      } catch (err) { fail(msg, err); }
    });
    actions.append(save);
    form.append(actions, msg);
    pf.append(form);

    // ---- documents
    const ds = section(role === "ngo" ? "Photo verification and documents" : "Photos and verification documents");
    ds.append(ui.el("p", { class: "note" }, "JPG, PNG or WEBP images; PDF is also accepted for documents. Max 5 MB each."));
    for (const [kind, req] of Object.entries(ob.requirements)) {
      const box = ui.el("div", { class: "doc-kind" });
      const docs = ob.documents.filter((d) => d.kind === kind);
      box.append(ui.el("h3", {}, req.label + (req.max > 1 ? ` (${docs.length}/${req.max})` : "")));
      for (const d of docs) {
        const row = ui.el("div", { class: "doc" });
        if (d.content_type.startsWith("image/")) row.append(ui.el("img", { src: "/api/documents/" + d.id, alt: req.label }));
        const link = ui.el("a", { href: "/api/documents/" + d.id, target: "_blank", rel: "noopener" }, d.filename);
        row.append(link, ui.el("span", { class: "note" }, Math.ceil(d.size_bytes / 1024) + " KB"));
        if (editable) {
          const del = ui.el("button", { type: "button", class: "small danger" }, "Remove");
          del.addEventListener("click", async () => { await ui.api("/documents/" + d.id, { method: "DELETE" }); await refresh(); });
          row.append(del);
        }
        box.append(row);
      }
      if (editable && (docs.length < req.max || req.max === 1)) {
        const row = ui.el("div", { class: "row" });
        const accept = req.allowed.join(",");
        const file = ui.el("input", { type: "file", accept });
        if (kind === "representative_photo") file.setAttribute("capture", "user");
        const btn = ui.el("button", { type: "button", class: "small" }, docs.length && req.max === 1 ? "Replace" : "Upload");
        const m = ui.el("div", { class: "msg" });
        btn.addEventListener("click", async () => {
          if (!file.files.length) return fail(m, new Error("Choose a file first."));
          const fd = new FormData();
          fd.append("kind", kind);
          fd.append("file", file.files[0]);
          btn.disabled = true;
          try { await ui.api("/documents", { method: "POST", body: fd }); await refresh(); }
          catch (e) { fail(m, e); btn.disabled = false; }
        });
        row.append(file, btn);
        box.append(row, m);
      }
      ds.append(box);
    }

    // ---- submit: last, after the details and documents are filled in
    if (editable) {
      const sb = section(ob.status === "on_hold" ? "Resubmit your application" : "Submit your application");
      sb.append(ui.el("p", { class: "note" }, ob.missing.length
        ? "Complete everything listed under Verification to enable this button."
        : "Everything is ready. Send your application to the host for review."));
      const submit = ui.el("button", { class: "primary", type: "button" }, ob.status === "on_hold" ? "Resubmit for review" : "Submit for review");
      submit.disabled = ob.missing.length > 0;
      const msg = ui.el("div", { class: "msg" });
      submit.addEventListener("click", async () => {
        try { await ui.api("/submit", { method: "POST" }); await refresh(); } catch (e) { fail(msg, e); }
      });
      sb.append(submit, msg);
    }
  }

  async function refresh() {
    await ui.refreshMe();  // re-renders the profile card, which calls onSignedIn -> render()
  }

  window.PORTAL.onSignedIn = (acc, helpers) => {
    ui = helpers;
    role = acc.role;
    root = document.getElementById("onboarding");
    render().catch((e) => { root.replaceChildren(ui.el("p", { class: "msg err" }, e.message)); });
    // Portal-specific features (food listings, NGO feed) register here.
    for (const extra of window.PORTAL.extras || []) extra(acc, helpers);
  };
})();
