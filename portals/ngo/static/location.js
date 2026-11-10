// NGO portal, Phase 5: share live location with the host map while on duty (Module 10).
(function () {
  const SEND_EVERY_MS = 20000;
  const KEY = "frs-share-location";
  let watchId = null;
  let lastSent = 0;
  let pending = null;
  let statusEl = null;   // re-pointed on every redraw so the running watcher always updates the visible page
  let api = null;
  let buttons = null;

  window.PORTAL.extras = window.PORTAL.extras || [];
  window.PORTAL.extras.push((acc, ui) => {
    if (acc.status !== "verified") return;
    const root = document.getElementById("food");
    const panel = ui.el("div", { class: "panel" });
    panel.append(ui.el("h2", {}, "Live location"));
    panel.append(ui.el("p", { class: "note" },
      "Lets the host see where you are on the map while you are Available or doing a pickup. " +
      "Your location is not shared when you stop, or when you switch to Unavailable with no pickup."));
    const status = ui.el("p", { class: "msg" });
    const row = ui.el("div", { class: "row" });
    const start = ui.el("button", { type: "button", class: "primary", style: "width:auto;margin-top:0" }, "Start sharing my location");
    const stop = ui.el("button", { type: "button" }, "Stop sharing");
    row.append(start, stop);
    panel.append(row, status);
    root.prepend(panel);

    statusEl = status;
    api = ui.api;
    buttons = [start, stop];
    const show = (text, ok) => { statusEl.textContent = text; statusEl.className = "msg " + (ok ? "ok" : "err"); };
    const sharing = () => watchId !== null;
    const refreshButtons = () => { buttons[0].disabled = sharing(); buttons[1].disabled = !sharing(); };
    if (sharing()) show("Sharing your location.", true);

    async function send(pos) {
      pending = pos;
      if (Date.now() - lastSent < SEND_EVERY_MS) return;  // throttle; the newest position is sent next time
      lastSent = Date.now();
      try {
        await api("/location", { method: "PUT", body: JSON.stringify({
          latitude: pending.coords.latitude, longitude: pending.coords.longitude, accuracy_m: pending.coords.accuracy }) });
        show(`Sharing — last sent ${new Date().toLocaleTimeString()}`, true);
      } catch (e) {
        show(e.message);
        if (/only while/.test(e.message)) stopSharing(false);
      }
    }

    function startSharing() {
      if (!navigator.geolocation) return show("This browser cannot share location.");
      lastSent = 0;
      watchId = navigator.geolocation.watchPosition(send,
        (err) => { show("Could not read your location: " + err.message); stopSharing(false); },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 });
      try { localStorage.setItem(KEY, "1"); } catch {}
      show("Waiting for your position…", true);
      refreshButtons();
    }

    async function stopSharing(tellServer = true) {
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      watchId = null;
      try { localStorage.removeItem(KEY); } catch {}
      if (tellServer) {
        try { await api("/location", { method: "DELETE" }); show("Stopped sharing. Your location was removed from the map.", true); }
        catch (e) { show(e.message); }
      }
      refreshButtons();
    }

    start.addEventListener("click", startSharing);
    stop.addEventListener("click", () => stopSharing(true));
    let remembered = false;
    try { remembered = localStorage.getItem(KEY) === "1"; } catch {}
    if (remembered && !sharing()) startSharing();
    refreshButtons();
  });
})();
