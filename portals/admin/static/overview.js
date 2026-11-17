// Host portal: the Overview page (first page after signing in). Key numbers, what needs attention,
// recent food and activity. Uses the same host API calls as the other pages.
(function () {
  const $ = (id) => document.getElementById(id);
  const ICON = {
    review: '<path d="M4 13.5 7 5h10l3 8.5V19a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19z"/><path d="M4 13.5h4.5l1.5 2.5h4l1.5-2.5H20"/>',
    food: '<path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M9.5 7.5c0-1.5 1-1.5 1-3M13.5 7.5c0-1.5 1-1.5 1-3"/>',
    truck: '<path d="M2.5 6.5h11v10h-11z"/><path d="M13.5 10h4l3 3.2v3.3h-7"/><circle cx="6.5" cy="17.5" r="1.8"/><circle cx="16.8" cy="17.5" r="1.8"/>',
    done: '<circle cx="12" cy="12" r="8.5"/><path d="m8.3 12.3 2.5 2.5 5-5.2"/>',
    people: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 20c0-3.4 2.7-6 6-6s6 2.6 6 6"/><circle cx="17.2" cy="9.3" r="2.5"/><path d="M16 14.3c2.8-.4 5 1.8 5 4.7"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  };
  const svg = (k) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;
  const STATUS = { active: "Waiting for NGO", assigned: "NGO assigned", collected: "Collected", delivered: "Delivered",
                   stored: "Stored at NGO", rejected: "Rejected", disposed: "Disposed", cancelled: "Cancelled", expired: "Expired" };
  const EVENT = {
    "account.registered": "New account registered", "application.submitted": "Application submitted",
    "application.reviewed": "Application reviewed", "auth.login": "Signed in", "listing.created": "Food posted",
    "listing.assigned": "NGO accepted food", "listing.collected": "Food collected", "listing.delivered": "Food delivered",
    "auth.password_reset": "Password reset",
  };
  const when = (iso) => {
    const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
  };

  async function show(ui, go) {
    const box = $("tab-overview");
    if (!box.dataset.built) {
      box.dataset.built = "1";
      box.innerHTML = `
        <div class="kpis" id="ov-kpis"></div>
        <div class="ov-grid">
          <div class="panel"><div class="panel-head"><h2>Needs your attention</h2></div><div id="ov-attention"></div></div>
          <div class="panel"><div class="panel-head"><h2>Latest food</h2><button type="button" class="link-btn" data-go="listings">View all ${svg("arrow")}</button></div><div id="ov-food"></div></div>
          <div class="panel ov-wide"><div class="panel-head"><h2>Recent activity</h2><button type="button" class="link-btn" data-go="events">View all ${svg("arrow")}</button></div><div id="ov-activity"></div></div>
        </div>`;
      box.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
    }
    let queue, listings, agents, accounts, events;
    try {
      [queue, listings, agents, accounts, events] = await Promise.all([
        ui.api("/review-queue?status=under_review"), ui.api("/listings"), ui.api("/ngo-directory"),
        ui.api("/accounts"), ui.api("/events?limit=8")]);
    } catch (e) { $("ov-kpis").textContent = e.message; return; }

    const open = listings.filter((l) => l.status === "active").length;
    const moving = listings.filter((l) => l.status === "assigned" || l.status === "collected").length;
    const done = listings.filter((l) => l.status === "delivered" || l.status === "stored").length;
    const meals = listings.filter((l) => (l.status === "delivered" || l.status === "stored") && l.unit === "portions")
      .reduce((n, l) => n + (Number(l.quantity) || 0), 0);
    const available = agents.filter((r) => r.account.badges && r.account.badges.availability
      && r.account.badges.availability.label === "Available").length;

    const kpis = [
      ["review", "Waiting for review", queue.length, queue.length ? "applications" : "all caught up", "queue", "c-amber"],
      ["food", "Open food", open, "waiting for an NGO", "listings", "c-orange"],
      ["truck", "On the way", moving, "pickups in progress", "map", "c-blue"],
      ["done", "Delivered", done, `${meals.toLocaleString("en-IN")} meals so far`, "listings", "c-green"],
      ["people", "NGO agents available", available, `of ${agents.length} agents`, "agents", "c-teal"],
    ];
    const k = $("ov-kpis");
    k.replaceChildren();
    for (const [ic, label, value, note, target, cls] of kpis) {
      const card = ui.el("button", { type: "button", class: "kpi " + cls });
      card.innerHTML = `<span class="kpi-ic">${svg(ic)}</span><span class="kpi-label"></span><b class="kpi-value"></b><small class="kpi-note"></small>`;
      card.querySelector(".kpi-label").textContent = label;
      card.querySelector(".kpi-value").textContent = value.toLocaleString("en-IN");
      card.querySelector(".kpi-note").textContent = note;
      card.addEventListener("click", () => go(target));
      k.append(card);
    }

    // needs attention: applications to review + food nobody has taken yet
    const att = $("ov-attention");
    att.replaceChildren();
    const items = [
      ...queue.slice(0, 4).map((a) => ({ t: a.account ? a.account.display_name : (a.display_name || "Application"),
        d: `${(a.account ? a.account.role : a.role) === "ngo" ? "NGO" : "Restaurant"} application`, go: "queue" })),
      ...listings.filter((l) => l.status === "active").slice(0, 4).map((l) => ({
        t: l.food_type, d: `${l.quantity} ${l.unit} at ${l.restaurant.name} - no NGO yet`, go: "map" })),
    ];
    if (!items.length) att.append(ui.el("p", { class: "empty" }, "Nothing needs you right now."));
    for (const it of items) {
      const row = ui.el("button", { type: "button", class: "ov-row" });
      row.innerHTML = `<span class="dot"></span><span class="ov-row-text"><b></b><small></small></span>${svg("arrow")}`;
      row.querySelector("b").textContent = it.t;
      row.querySelector("small").textContent = it.d;
      row.addEventListener("click", () => go(it.go));
      att.append(row);
    }

    // latest food
    const food = $("ov-food");
    food.replaceChildren();
    if (!listings.length) food.append(ui.el("p", { class: "empty" }, "No food has been posted yet."));
    for (const l of listings.slice(0, 5)) {
      const row = ui.el("div", { class: "ov-row static" });
      row.innerHTML = `<span class="ov-row-text"><b></b><small></small></span><span class="status ${l.status}"></span>`;
      row.querySelector("b").textContent = l.food_type;
      row.querySelector("small").textContent = `${l.restaurant.name} · ${l.quantity} ${l.unit} · ${when(l.created_at)}`;
      row.querySelector(".status").textContent = STATUS[l.status] || l.status;
      food.append(row);
    }

    // recent activity
    const names = Object.fromEntries(accounts.map((a) => [a.id, a.display_name]));
    const act = $("ov-activity");
    act.replaceChildren();
    if (!events.length) act.append(ui.el("p", { class: "empty" }, "No activity yet."));
    const ul = ui.el("ul", { class: "timeline-list" });
    for (const ev of events) {
      const li = ui.el("li");
      li.innerHTML = `<span class="tl-dot"></span><span class="ov-row-text"><b></b><small></small></span><time></time>`;
      li.querySelector("b").textContent = EVENT[ev.type] || ev.type.replace(/[._]/g, " ");
      li.querySelector("small").textContent = names[ev.subject_id] || (ev.portal ? ev.portal + " portal" : "");
      li.querySelector("time").textContent = when(ev.created_at);
      ul.append(li);
    }
    act.append(ul);
  }

  window.FRSOverview = { show };
})();
