// Sign-in screen for all portals: email/phone + password, create account, forgot/reset password,
// one-time codes by email, and Continue with Google (only when the server has a Google client ID).
(function () {
  const $ = (id) => document.getElementById(id);
  const ui = window.FRS;
  const views = () => document.querySelectorAll("#auth .auth-view");

  function show(view) {
    views().forEach((v) => { v.hidden = v.dataset.view !== view; });
    document.querySelectorAll("#auth .msg").forEach((m) => { m.textContent = ""; m.className = "msg"; });
    const first = document.querySelector(`#auth .auth-view[data-view="${view}"] input`);
    if (first) setTimeout(() => first.focus(), 0);
  }

  async function post(path, body) {
    return ui.api(path, { method: "POST", body: JSON.stringify(body) });
  }

  function busy(form, on) {
    const b = form.querySelector('button[type="submit"]');
    if (b) { b.disabled = on; b.classList.toggle("is-busy", on); }
  }

  async function submit(form, msgId, run) {
    busy(form, true);
    try { await run(); } catch (err) { ui.showMsg(msgId, err.message); } finally { busy(form, false); }
  }

  function signedIn(acc, form) {
    if (form) form.reset();
    history.replaceState(null, "", location.pathname);  // drop any #reset=... from the address bar
    show("login");
    ui.signedIn(acc);
  }

  // ---- sign in with email or phone number + password
  $("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    submit(f, "login-msg", async () => signedIn(await post("/login", { email: f.email.value.trim(), password: f.password.value }), f));
  });

  // ---- create account
  if ($("register-form")) {
    $("register-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = e.target;
      if (f.password.value !== f.confirm.value) return ui.showMsg("register-msg", "The passwords don't match.");
      submit(f, "register-msg", async () => signedIn(await post("/register", {
        display_name: f.display_name.value, email: f.email.value, phone: f.phone.value || null, password: f.password.value }), f));
    });
  }

  // ---- forgot password: email a reset link
  $("forgot-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    submit(f, "forgot-msg", async () => {
      const r = await post("/password/forgot", { email: f.email.value.trim() });
      ui.showMsg("forgot-msg", r.detail + " Check your inbox (and spam folder).", true);
    });
  });

  // ---- reset link opened: choose a new password
  let resetToken = null;
  $("reset-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.password.value !== f.confirm.value) return ui.showMsg("reset-msg", "The passwords don't match.");
    submit(f, "reset-msg", async () => signedIn(await post("/password/reset", { token: resetToken, password: f.password.value }), f));
  });

  // ---- SMS code: first send, then check
  let codeSent = false;
  function codeStep(sent) {
    codeSent = sent;
    $("code-step").hidden = !sent;
    $("c-code").required = sent;
    $("code-submit").textContent = sent ? "Sign in" : "Send code";
    if (sent) setTimeout(() => $("c-code").focus(), 0);
  }
  async function sendCode() {
    const r = await post("/login/code/start", { identifier: $("c-phone").value.trim() });
    ui.showMsg("code-msg", r.detail, true);
    codeStep(true);
  }
  $("code-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    submit(f, "code-msg", async () => {
      if (!codeSent) return sendCode();
      signedIn(await post("/login/code/verify", { identifier: f.identifier.value.trim(), code: f.code.value.trim() }), f);
    });
  });
  $("code-resend").addEventListener("click", (e) => { e.preventDefault(); submit($("code-form"), "code-msg", sendCode); });
  $("c-phone").addEventListener("input", () => { if (codeSent) codeStep(false); });

  // ---- links between the views
  document.querySelectorAll("#auth [data-go]").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    if (a.dataset.go === "code") { codeStep(false); if ($("l-email").value.trim()) $("c-phone").value = $("l-email").value.trim(); }
    if (a.dataset.go === "forgot" && $("l-email").value.includes("@")) $("f-email").value = $("l-email").value.trim();
    show(a.dataset.go);
  }));

  // ---- Continue with Google (Google Identity Services)
  function startGoogle(clientId) {
    const holder = $("google-btn");
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => {
      google.accounts.id.initialize({
        client_id: clientId,
        callback: (res) => submit($("login-form"), "login-msg",
          async () => signedIn(await post("/login/google", { credential: res.credential }))),
      });
      google.accounts.id.renderButton(holder, { type: "standard", theme: "outline", size: "large", text: "continue_with",
                                                shape: "pill", logo_alignment: "center", width: holder.clientWidth || 320 });
    };
    s.onerror = () => { holder.hidden = true; };
    document.head.append(s);
    holder.hidden = false;
  }

  ui.api("/portal").then((info) => {
    const google = !!info.google_client_id, code = info.code_sign_in !== false;
    $("auth-alt").hidden = !(google || code);
    $("code-open").hidden = !code;
    if (google) startGoogle(info.google_client_id);
  }).catch(() => {});

  // a reset link looks like  https://portal/#reset=<token>
  const m = location.hash.match(/^#reset=([\w.-]+)$/);
  if (m) { resetToken = m[1]; show("reset"); } else show("login");
})();
