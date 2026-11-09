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
      const submit = ui.el("button", { class: "primary", type: "button" }, ob.status === "on_hold" ? "Resubmit for review" : "Submit for review");
      submit.disabled = ob.missing.length > 0;
      const msg = ui.el("div", { class: "msg" });
      submit.addEventListener("click", async () => {
        try { await ui.api("/submit", { method: "POST" }); await refresh(); } catch (e) { fail(msg, e); }
      });
      st.append(submit, msg);
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
    for (const [name, label, kind] of FIELDS[role]) {
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
    const locate = ui.el("button", { type: "button" }, "Use my current location");
    const save = ui.el("button", { type: "submit", class: "primary", style: "width:auto;margin-top:0" }, "Save details");
    const msg = ui.el("div", { class: "msg", style: "grid-column:1/-1" });
    locate.disabled = save.disabled = !editable;
    locate.addEventListener("click", () => {
      if (!navigator.geolocation) return fail(msg, new Error("Location is not available in this browser."));
      navigator.geolocation.getCurrentPosition(
        (p) => { form.latitude.value = p.coords.latitude.toFixed(6); form.longitude.value = p.coords.longitude.toFixed(6); },
        () => fail(msg, new Error("Could not read your location; enter latitude and longitude manually.")));
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {};
      for (const [name, , kind] of FIELDS[role]) body[name] = kind === "num" ? Number(form[name].value) : form[name].value;
      try {
        await ui.api("/profile", { method: "PUT", body: JSON.stringify(body) });
        await refresh();
      } catch (err) { fail(msg, err); }
    });
    actions.append(locate, save);
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
  }

  async function refresh() {
    await ui.refreshMe();  // re-renders the profile card, which calls onSignedIn -> render()
  }

  window.PORTAL.onSignedIn = (acc, helpers) => {
    ui = helpers;
    role = acc.role;
    root = document.getElementById("onboarding");
    render().catch((e) => { root.replaceChildren(ui.el("p", { class: "msg err" }, e.message)); });
  };
})();
