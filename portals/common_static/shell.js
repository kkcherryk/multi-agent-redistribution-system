// App layout for signed-in users (all portals): left sidebar with icons, top bar with page title,
// notifications and a profile menu. It only rearranges the page; every feature keeps working as before.
(function () {
  const $ = (id) => document.getElementById(id);
  const P = {
    overview: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    map: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    queue: '<path d="M4 13.5 7 5h10l3 8.5V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19z"/><path d="M4 13.5h4.5l1.5 2.5h4l1.5-2.5H20"/>',
    listings: '<path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M9.5 7.5c0-1.5 1-1.5 1-3M13.5 7.5c0-1.5 1-1.5 1-3"/>',
    "agents-ai": '<rect x="5" y="7" width="14" height="12" rx="3"/><path d="M12 7V4M9 12h.01M15 12h.01M9.5 15.5h5"/>',
    trust: '<path d="M12 3.2 19 6v5.2c0 4.3-2.9 7.8-7 9.6-4.1-1.8-7-5.3-7-9.6V6z"/><path d="m9 12 2.2 2.2L15.3 10"/>',
    simulation: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    storage: '<path d="M3.5 7.8 12 4l8.5 3.8L12 11.6z"/><path d="M3.5 7.8v8.4L12 20l8.5-3.8V7.8M12 11.6V20"/>',
    agents: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 20c0-3.4 2.7-6 6-6s6 2.6 6 6"/><circle cx="17.2" cy="9.3" r="2.5"/><path d="M16 14.3c2.8-.4 5 1.8 5 4.7"/>',
    accounts: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7"/>',
    events: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    privacy: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    doc: '<path d="M6 3.5h8l4 4V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20z"/><path d="M14 3.5V8h4M9 13h6M9 16.5h6"/>',
    truck: '<path d="M2.5 6.5h11v10h-11z"/><path d="M13.5 10h4l3 3.2v3.3h-7"/><circle cx="6.5" cy="17.5" r="1.8"/><circle cx="16.8" cy="17.5" r="1.8"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.8h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    toggle: '<rect x="2.5" y="7" width="19" height="10" rx="5"/><circle cx="16.5" cy="12" r="3"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    out: '<path d="M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
    chevron: '<path d="m7 10 5 5 5-5"/>',
  };
  const icon = (k) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${P[k] || P.doc}</svg>`;
  // pick an icon for a section heading on the restaurant / NGO pages
  function iconFor(text) {
    const t = text.toLowerCase();
    if (/food|listing|post|surplus/.test(t)) return "listings";
    if (/pickup|collect|deliver|on the way/.test(t)) return "truck";
    if (/near|feed|available food/.test(t)) return "bell";
    if (/photo|document|upload/.test(t)) return "doc";
    if (/verif|application|submit/.test(t)) return "trust";
    if (/availab/.test(t)) return "toggle";
    if (/storage|stored|stock/.test(t)) return "storage";
    if (/location|map/.test(t)) return "map";
    if (/detail|profile|representative/.test(t)) return "accounts";
    if (/backup/.test(t)) return "agents";
    return "doc";
  }
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

  function build() {
    const header = document.querySelector("header"), dash = $("dashboard");
    if (!header || !dash || $("side")) return;
    const brand = header.querySelector("h1");

    // sidebar
    const side = el("aside", "side"); side.id = "side";
    const sideBrand = brand.cloneNode(true); sideBrand.classList.add("side-brand");
    const nav = el("nav", "side-nav"); nav.setAttribute("aria-label", "Sections");
    side.append(sideBrand, nav);
    const scrim = el("div", "side-scrim");
    document.body.prepend(side, scrim);

    // top bar: menu button + page title on the left; bell + profile menu on the right
    const lead = el("div", "top-lead");
    const burger = el("button", "top-burger", icon("menu")); burger.type = "button"; burger.setAttribute("aria-label", "Open menu");
    const titles = el("div", "top-titles");
    const title = el("h2", "top-title", "Dashboard"); const sub = el("p", "top-sub", "");
    titles.append(title, sub); lead.append(burger, titles);
    header.prepend(lead);

    const acct = el("div", "acct");
    const acctBtn = el("button", "acct-btn"); acctBtn.type = "button"; acctBtn.setAttribute("aria-haspopup", "true");
    const menu = el("div", "acct-menu"); menu.hidden = true;
    acct.append(acctBtn, menu);
    const right = header.querySelector("header > div:last-child");
    right.append(acct);
    // the profile card and Sign out live in the profile menu
    const profilePanel = $("welcome").closest(".panel");
    const logout = $("logout");
    menu.append(profilePanel, logout);
    logout.innerHTML = icon("out") + "Sign out";
    logout.classList.add("acct-out");

    const toggleMenu = (open) => { menu.hidden = !open; acctBtn.setAttribute("aria-expanded", open); };
    acctBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMenu(menu.hidden); });
    document.addEventListener("click", (e) => { if (!acct.contains(e.target)) toggleMenu(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { toggleMenu(false); document.body.classList.remove("side-open"); } });
    const closeSide = () => document.body.classList.remove("side-open");
    burger.addEventListener("click", () => document.body.classList.toggle("side-open"));
    scrim.addEventListener("click", closeSide);

    function syncIdentity() {
      const name = ($("welcome").textContent || "").replace(/^Welcome,\s*/, "") || "Account";
      const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
      const role = { admin: "Host", restaurant: "Restaurant", ngo: "NGO" }[document.body.dataset.portal] || "";
      acctBtn.innerHTML = `<span class="avatar">${initials}</span><span class="acct-text"><b></b><small></small></span>${icon("chevron")}`;
      acctBtn.querySelector("b").textContent = name;
      acctBtn.querySelector("small").textContent = role;
      sub.textContent = `Welcome back, ${name.split(/\s+/)[0]}`;
    }

    // ---- navigation
    const tabs = $("admin-tabs");
    if (tabs) {
      // host: the existing tab buttons become the sidebar menu
      nav.append(tabs);
      tabs.classList.add("side-tabs");
      tabs.querySelectorAll("button").forEach((b) => {
        if (!b.querySelector(".ic")) b.insertAdjacentHTML("afterbegin", icon(b.dataset.tab));
        b.addEventListener("click", () => { setTitle(); closeSide(); window.scrollTo({ top: 0 }); });
      });
      const setTitle = () => {
        const a = tabs.querySelector("button.active");
        title.textContent = a ? a.textContent.replace(/\d+$/, "").trim() : "Dashboard";
      };
      setTitle();
      new MutationObserver(setTitle).observe(tabs, { attributes: true, subtree: true, attributeFilter: ["class"] });
    } else {
      // restaurant / NGO: one link per section on the page, scrolling to it
      const list = el("div", "side-tabs");
      nav.append(list);
      let timer = null;
      const rebuild = () => {
        const heads = [...dash.querySelectorAll(".panel > h2")].filter((h) => h.id !== "welcome" && h.offsetParent !== null);
        list.replaceChildren();
        heads.forEach((h, i) => {
          if (!h.id) h.id = "sec-" + i + "-" + h.textContent.toLowerCase().replace(/[^a-z]+/g, "-").slice(0, 30);
          const b = el("button", "", icon(iconFor(h.textContent)) + `<span></span>`); b.type = "button";
          b.querySelector("span").textContent = h.textContent;
          b.dataset.target = h.id;
          b.addEventListener("click", () => {
            const panel = h.closest(".panel");
            window.scrollTo({ top: panel.getBoundingClientRect().top + scrollY - 88, behavior: "smooth" });
            closeSide();
          });
          list.append(b);
        });
        markActive();
      };
      const markActive = () => {
        const btns = [...list.querySelectorAll("button")];
        let current = btns[0];
        for (const b of btns) {
          const t = $(b.dataset.target);
          if (t && t.closest(".panel").getBoundingClientRect().top < 140) current = b;
        }
        btns.forEach((b) => b.classList.toggle("active", b === current));
      };
      new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(rebuild, 120); })
        .observe(dash, { childList: true, subtree: true });
      addEventListener("scroll", () => requestAnimationFrame(markActive), { passive: true });
      rebuild();
    }

    // signed in <-> signed out
    const sync = () => {
      const on = !dash.classList.contains("hidden");
      document.body.classList.toggle("app", on);
      if (on) syncIdentity(); else { toggleMenu(false); closeSide(); }
    };
    new MutationObserver(sync).observe(dash, { attributes: true, attributeFilter: ["class"] });
    new MutationObserver(syncIdentity).observe($("welcome"), { childList: true, characterData: true, subtree: true });
    sync();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build); else build();
})();
