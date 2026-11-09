// Shared UI logic for the portal frontends. Each portal page sets window.PORTAL before loading this file.
(function () {
  const cfg = window.PORTAL;
  const $ = (id) => document.getElementById(id);
  const STATUS_LABEL = { under_review: "Under review", on_hold: "On hold", verified: "Verified", declined: "Declined" };

  async function api(path, opts = {}) {
    const res = await fetch("/api" + path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      let detail = body.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => (d.loc || []).slice(-1)[0] + ": " + d.msg).join("; ");
      throw new Error(detail || "Request failed (" + res.status + ")");
    }
    return body;
  }

  function el(tag, attrs = {}, text) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function statusChip(status) {
    return el("span", { class: "status " + status }, STATUS_LABEL[status] || status);
  }

  function showMsg(id, text, ok) {
    const m = $(id);
    m.textContent = text;
    m.className = "msg " + (ok ? "ok" : "err");
  }

  function setMode(mode) {
    $("login-form").classList.toggle("hidden", mode !== "login");
    if ($("register-form")) $("register-form").classList.toggle("hidden", mode !== "register");
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  }

  function renderProfile(acc) {
    $("auth").classList.add("hidden");
    $("dashboard").classList.remove("hidden");
    $("logout").classList.remove("hidden");
    $("welcome").textContent = "Welcome, " + acc.display_name;
    const dl = $("profile");
    dl.replaceChildren();
    const rows = [["Role", acc.role], ["Email", acc.email], ["Phone", acc.phone || "—"], ["Account status", null],
                  ["Member since", new Date(acc.created_at).toLocaleString()]];
    for (const [k, v] of rows) {
      dl.append(el("dt", {}, k));
      const dd = el("dd");
      if (v === null) dd.append(statusChip(acc.status)); else dd.textContent = v;
      dl.append(dd);
    }
    $("pending-note").classList.toggle("hidden", acc.status === "verified");
    if (cfg.onSignedIn) cfg.onSignedIn(acc, { api, el, statusChip });
  }

  function renderSignedOut() {
    $("auth").classList.remove("hidden");
    $("dashboard").classList.add("hidden");
    $("logout").classList.add("hidden");
  }

  async function init() {
    document.title = cfg.title;
    api("/portal").then((info) => { $("home").href = info.landing_url; }).catch(() => {});
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

    $("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      try {
        const acc = await api("/login", { method: "POST", body: JSON.stringify({ email: f.email.value, password: f.password.value }) });
        f.reset();
        renderProfile(acc);
      } catch (err) { showMsg("login-msg", err.message); }
    });

    if ($("register-form")) {
      $("register-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.target;
        if (f.password.value !== f.confirm.value) return showMsg("register-msg", "Passwords do not match.");
        try {
          const acc = await api("/register", { method: "POST", body: JSON.stringify({
            display_name: f.display_name.value, email: f.email.value, phone: f.phone.value || null, password: f.password.value }) });
          f.reset();
          renderProfile(acc);
        } catch (err) { showMsg("register-msg", err.message); }
      });
    }

    $("logout").addEventListener("click", async () => {
      await api("/logout", { method: "POST" }).catch(() => {});
      renderSignedOut();
    });

    try { renderProfile(await api("/me")); } catch { renderSignedOut(); }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
