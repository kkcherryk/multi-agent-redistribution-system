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

  // counters
  const cio = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) {
      const el = en.target, to = +el.dataset.to, suf = el.dataset.suffix || "";
      cio.unobserve(el);
      if (reduce) { el.textContent = to + suf; continue; }
      const t0 = performance.now(), dur = 1600;
      const tick = (t) => {
        const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
        el.textContent = Math.round(to * e) + suf;
        if (k < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
  }, { threshold: 0.5 });
  $$(".count").forEach((n) => cio.observe(n));

  $("#year").textContent = new Date().getFullYear();
})();
