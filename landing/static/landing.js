// Landing page interactions: sticky nav, menu, hero slideshow, scroll reveals, parallax, timeline, counters.
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // nav: solid after scrolling, current-section highlight
  const nav = $("#nav");
  const navLinks = $$(".links>a[href^='#']:not(.btn)");
  const onScroll = () => nav.classList.toggle("solid", scrollY > 40);
  addEventListener("scroll", onScroll, { passive: true }); onScroll();

  // mobile menu
  const menuBtn = $("#menu-btn"), links = $("#links");
  menuBtn.addEventListener("click", () => {
    const open = links.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", open);
  });
  links.addEventListener("click", (e) => { if (e.target.matches("a[href^='#']")) links.classList.remove("open"); });

  // log-in chooser
  const loginBtn = $("#login-btn"), loginMenu = $("#login-menu");
  loginBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = loginMenu.classList.toggle("open");
    loginBtn.setAttribute("aria-expanded", open);
  });
  document.addEventListener("click", (e) => {
    if (!loginMenu.contains(e.target)) { loginMenu.classList.remove("open"); loginBtn.setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { loginMenu.classList.remove("open"); links.classList.remove("open"); } });

  // hero slideshow
  const slides = $$(".slide"), dots = $$("#hero-dots i");
  let cur = 0;
  if (!reduce && slides.length > 1) setInterval(() => {
    slides[cur].classList.remove("on"); dots[cur].classList.remove("on");
    cur = (cur + 1) % slides.length;
    slides[cur].classList.add("on"); dots[cur].classList.add("on");
  }, 6000);

  // reveal on scroll
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
  }, { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
  $$(".reveal,.reveal-row,.role").forEach((n) => io.observe(n));
  $$(".role").forEach((n) => n.classList.add("reveal-flow"));
  $$(".flow li").forEach((li, i) => (li.style.transitionDelay = 0.25 + (i % 6) * 0.08 + "s"));

  // section highlight in nav
  const secIo = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) {
      navLinks.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + en.target.id));
    }
  }, { rootMargin: "-45% 0px -50% 0px" });
  ["home", "about", "how", "impact", "contact"].forEach((id) => { const s = document.getElementById(id); if (s) secIo.observe(s); });

  // parallax images + timeline progress (one rAF loop on scroll)
  const par = $$(".parallax img");
  const steps = $$("#timeline li"), fill = $("#timeline-fill"), tl = $("#timeline");
  let ticking = false;
  function frame() {
    ticking = false;
    const vh = innerHeight;
    if (!reduce) for (const img of par) {
      const r = img.parentElement.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) continue;
      const p = (r.top + r.height / 2 - vh / 2) / vh; // -1..1 around centre
      img.style.transform = `translateY(${(-6 + p * -8).toFixed(2)}%)`;
    }
    if (tl) {
      const r = tl.getBoundingClientRect();
      const prog = Math.min(1, Math.max(0, (vh * 0.85 - r.top) / (vh * 0.6)));
      const lit = Math.round(prog * steps.length);
      steps.forEach((li, i) => li.classList.toggle("lit", reduce || i < lit));
      if (fill) fill.style.width = (reduce ? 100 : prog * 100) + "%";
    }
  }
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(frame); } }, { passive: true });
  addEventListener("resize", frame); frame();

  // impact counters: real totals from the platform, refreshed every minute
  const fmt = (n) => Math.round(n).toLocaleString("en-IN");
  const plural = (n, word) => fmt(n) + " " + word + (n === 1 ? "" : "s");
  function animate(el, to) {
    const from = +(el.dataset.now || 0);
    el.dataset.now = to;
    if (reduce || from === to) { el.textContent = fmt(to); return; }
    const t0 = performance.now(), dur = from ? 900 : 1600;
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  const statsBox = $("#stats");
  let statsSeen = false, latest = null;
  function paint() {
    if (!latest || !statsSeen) return;
    $$("#stats .count").forEach((el) => animate(el, latest[el.dataset.key] || 0));
  }
  function showImpact(d) {
    latest = d;
    const empty = !d.donations_completed;
    statsBox.removeAttribute("aria-busy");
    $("#impact-error").hidden = true;
    statsBox.hidden = empty;
    $("#impact-empty").hidden = !empty;
    if (empty) {
      $("#impact-empty-text").textContent = (d.restaurants || d.ngos)
        ? plural(d.restaurants, "restaurant") + " and " + plural(d.ngos, "NGO") + " are ready. The very first meal saved could be yours."
        : "No donations have been completed yet. The very first meal saved could be yours.";
    }
    const kg = $("#kg-extra");
    kg.hidden = !(d.kg_delivered > 0);
    if (d.kg_delivered > 0) kg.textContent = "+ " + d.kg_delivered.toLocaleString("en-IN") + " kg of food";
    const parts = [];
    if (d.now_waiting) parts.push(plural(d.now_waiting, "donation") + " waiting for pickup");
    if (d.now_on_the_way) parts.push(fmt(d.now_on_the_way) + " on the way");
    $("#impact-live").hidden = !parts.length;
    if (parts.length) $("#impact-live-text").textContent = "Right now: " + parts.join(" · ");
    paint();
  }
  async function loadImpact() {
    try {
      const r = await fetch("/api/impact", { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(String(r.status));
      showImpact(await r.json());
    } catch {
      if (!latest) { statsBox.hidden = true; $("#impact-error").hidden = false; }
    }
  }
  new IntersectionObserver((entries, obs) => {
    if (entries.some((e) => e.isIntersecting)) { statsSeen = true; paint(); obs.disconnect(); }
  }, { threshold: 0.3 }).observe($("#impact"));
  loadImpact();
  setInterval(() => { if (!document.hidden) loadImpact(); }, 60000);

  $("#year").textContent = new Date().getFullYear();
})();
