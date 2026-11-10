// Host portal, Phase 5: live operations map (Module 10). Uses Leaflet + OpenStreetMap tiles (no API key).
(function () {
  const REFRESH_MS = 10000;
  const COLORS = { restaurant: "#1f6feb", active: "#c4620a", assigned: "#8250df", collected: "#1a7f37",
                   available: "#1a7f37", unavailable: "#cf222e" };
  const STEP = { accepted: "accepted, not started", en_route: "on the way to the restaurant", collected: "food collected, delivering" };
  let map = null;
  let layer = null;
  let timer = null;
  let fitted = false;
  let selected = null;
  let last = null;
  let lastBounds = [];
  let autoFit = true;

  // Zoom to the markers once, but only when the map has a real size (it has none while its tab is hidden).
  function fitOnce() {
    const size = map.getSize();
    if (fitted || !lastBounds.length || size.x === 0 || size.y === 0) return;
    map.fitBounds(lastBounds, { padding: [30, 30], maxZoom: 15, animate: false });
    fitted = true;
  }

  const $ = (id) => document.getElementById(id);
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function agentPoint(a) {
    if (a.location) return { lat: a.location.latitude, lon: a.location.longitude, kind: a.location.live ? "live" : "last" };
    return { lat: a.base.latitude, lon: a.base.longitude, kind: "base" };
  }

  function ago(seconds) {
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    return `${Math.round(seconds / 3600)} h ago`;
  }

  function renderPanel(ui) {
    const panel = $("agent-panel");
    panel.replaceChildren();
    const a = last && last.agents.find((x) => x.id === selected);
    if (!a) {
      panel.append(ui.el("p", { class: "note" }, "Click an NGO agent on the map to see their details."));
      return;
    }
    panel.append(ui.el("h3", { style: "margin-top:0" }, a.representative_name));
    panel.append(ui.el("div", { class: "note" }, a.ngo_name));
    panel.append(ui.el("div", { class: "badges", style: "margin:8px 0" }));
    panel.lastChild.append(ui.badge({ color: "green", label: "Verified NGO" }),
                           ui.badge(a.available ? { color: "green", label: "Available" } : { color: "red", label: "Unavailable" }));
    const dl = ui.el("dl");
    const p = agentPoint(a);
    const where = p.kind === "live" ? `Live (${ago(a.location.age_seconds)})`
      : p.kind === "last" ? `Last known, ${ago(a.location.age_seconds)} — not live`
      : "Not sharing location — shown at NGO base";
    const rows = [["Phone", a.phone || "—"], ["Location", where], ["NGO base", a.base.address],
                  ["Current pickup", a.current_pickup ? `${a.current_pickup.food_type} — ${STEP[a.current_pickup.step] || a.current_pickup.step}` : "None"],
                  ["Deliveries", String(a.stats.deliveries)], ["Stored", String(a.stats.stored)], ["Disposed", String(a.stats.disposed)]];
    for (const [k, v] of rows) dl.append(ui.el("dt", {}, k), ui.el("dd", {}, v));
    panel.append(dl);
  }

  // Re-measure the map box; when it changed, re-fit to the markers (until the host moves the map themselves).
  function syncSize() {
    const el = $("map");
    if (!map || !el.clientWidth || !el.clientHeight) return;
    const before = map.getSize();
    map.invalidateSize({ pan: false });
    const after = map.getSize();
    if (autoFit && (before.x !== after.x || before.y !== after.y)) fitted = false;
    fitOnce();
  }

  function draw(ui, data) {
    syncSize();
    last = data;
    layer.clearLayers();
    const bounds = [];
    const s = data.summary;
    $("map-summary").replaceChildren(
      ...[["Open food", s.open_food], ["In progress", s.in_progress], ["Delivered", s.delivered], ["Stored", s.stored],
          ["Agents available", s.agents_available], ["Agents live", s.agents_live]]
        .map(([k, v]) => { const c = ui.el("span", { class: "pill" }); c.textContent = `${k}: ${v}`; return c; }),
      ui.el("span", { class: "note" }, ` updated ${fmtTime(data.generated_at)}`));

    for (const r of data.restaurants) {
      bounds.push([r.latitude, r.longitude]);
      L.circleMarker([r.latitude, r.longitude], { radius: 7, color: COLORS.restaurant, fillColor: COLORS.restaurant, fillOpacity: 0.9, weight: 2 })
        .bindPopup(`<strong>${esc(r.name)}</strong><br>Restaurant<br>${esc(r.address)}<br>Open listings: ${r.open_listings}`)
        .addTo(layer);
    }

    // Food tickets: slightly offset so they don't hide the restaurant dot.
    data.listings.forEach((l, i) => {
      const pos = [l.latitude + 0.0004 * (1 + (i % 3)), l.longitude + 0.0004];
      bounds.push(pos);
      if (l.trail.length > 1) L.polyline(l.trail, { color: COLORS[l.status], weight: 3, dashArray: "6 6" }).addTo(layer);
      L.circleMarker(pos, { radius: 6, color: "#fff", weight: 1, fillColor: COLORS[l.status], fillOpacity: 1 })
        .bindPopup(`<strong>${esc(l.food_type)}</strong> — ${l.quantity} ${esc(l.unit)}<br>${esc(l.restaurant_name)}<br>` +
                   `Status: ${esc(l.status)}${l.step ? " (" + esc(STEP[l.step] || l.step) + ")" : ""}<br>Best before ${fmtTime(l.expires_at)}`)
        .addTo(layer);
    });

    for (const a of data.agents) {
      const p = agentPoint(a);
      bounds.push([p.lat, p.lon]);
      const color = a.available ? COLORS.available : COLORS.unavailable;
      const style = p.kind === "live" ? { radius: 10, color: "#fff", weight: 3, fillColor: color, fillOpacity: 1 }
        : p.kind === "last" ? { radius: 9, color, weight: 2, dashArray: "3 3", fillColor: color, fillOpacity: 0.35 }
        : { radius: 8, color, weight: 2, fillColor: "#fff", fillOpacity: 0.6 };
      const m = L.circleMarker([p.lat, p.lon], style).addTo(layer);
      m.bindTooltip(`${a.representative_name} (${a.ngo_name})`);
      m.on("click", () => { selected = a.id; renderPanel(ui); });
    }

    lastBounds = bounds;
    fitOnce();
    renderPanel(ui);
  }

  async function refresh(ui) {
    try { draw(ui, await ui.api("/map")); }
    catch (e) { $("map-summary").textContent = "Could not load the map: " + e.message; }
  }

  // Called by admin.js when the "Live map" tab is shown or hidden.
  window.FRSMap = {
    show(ui) {
      if (!window.L) { $("map-summary").textContent = "The map library could not be loaded (no internet connection?)."; return; }
      if (!map) {
        map = L.map("map", { scrollWheelZoom: true }).setView([12.9716, 77.5946], 12);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(map);
        layer = L.layerGroup().addTo(map);
        const stopAutoFit = () => { autoFit = false; };
        map.on("dragstart", stopAutoFit);
        $("map").addEventListener("wheel", stopAutoFit, { passive: true });
        $("map").addEventListener("dblclick", stopAutoFit);
        // Leaflet measures its box only on window resizes. The box also changes when the tab is shown or the
        // page layout settles, so re-measure whenever it changes; otherwise markers are drawn for a stale size.
        new ResizeObserver(syncSize).observe($("map"));
      }
      refresh(ui);
      clearInterval(timer);
      timer = setInterval(() => refresh(ui), REFRESH_MS);
    },
    hide() { clearInterval(timer); timer = null; },
  };
})();
