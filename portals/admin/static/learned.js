// Host portal, Phase 9: learned dispatch - train in the simulator, read the test result, switch on/off.
(function () {
  const $ = (id) => document.getElementById(id);
  let ui = null;
  let chart = null;

  function render(s) {
    const box = $("learned-box");
    box.replaceChildren();
    const head = ui.el("div", { class: "row" });
    head.append(ui.el("strong", {}, "Learned dispatch"),
                ui.badge(s.enabled ? { color: "green", label: "On" } : { color: "grey", label: "Off" }));
    box.append(head, ui.el("p", { class: "note" },
      "A reinforcement-learning policy trained in the simulator decides who is offered food first. " +
      "When it is off (or not trained), the normal rule is used. People still press Accept, and the backup agent still works."));

    const row = ui.el("div", { class: "row" });
    const train = ui.el("button", { type: "button" }, s.trained ? "Train again" : "Train now (about 20 seconds)");
    const toggle = ui.el("button", { type: "button", class: s.enabled ? "" : "primary", style: "width:auto;margin-top:0" },
                         s.enabled ? "Switch off" : "Switch on");
    toggle.disabled = !s.trained;
    const msg = ui.el("div", { class: "msg" });
    row.append(toggle, train);
    box.append(row, msg);

    train.addEventListener("click", async () => {
      train.disabled = toggle.disabled = true;
      msg.textContent = "Practising on pretend cities and testing on new ones…";
      msg.className = "msg";
      try { render(await ui.api("/learned-dispatch/train", { method: "POST", body: JSON.stringify({ episodes: 300 }) })); }
      catch (e) { msg.textContent = e.message; msg.className = "msg err"; train.disabled = false; toggle.disabled = !s.trained; }
    });
    toggle.addEventListener("click", async () => {
      const turningOn = !s.enabled;
      if (turningOn && s.policy && s.policy.test && !s.policy.test.recommended &&
          !confirm("The test result does not recommend this policy. Switch it on anyway?")) return;
      try { render(await ui.api("/learned-dispatch", { method: "PUT", body: JSON.stringify({ enabled: turningOn }) })); }
      catch (e) { msg.textContent = e.message; msg.className = "msg err"; }
    });

    if (!s.policy) return;
    const p = s.policy;
    const t = p.test;
    if (!t || !p.explanation) {  // valid weights but no training report (e.g. a hand-made policy file)
      box.append(ui.el("p", { class: "note" }, "This policy file has no training report. Train again to see results."));
      return;
    }
    box.append(ui.el("div", { class: "callout " + (t.recommended ? "ok" : "err") }, t.summary));
    const ul = ui.el("ul", { class: "checklist" });
    t.details.forEach((d) => ul.append(ui.el("li", {}, d)));
    box.append(ul, ui.el("p", { class: "note" },
      `Trained ${new Date(p.trained_at).toLocaleString()} on ${p.episodes} practice days; tested on ${t.config.runs} unseen cities × ${t.config.days} days.`));

    const tbl = ui.el("table");
    const hr = ui.el("tr");
    ["Factor", "Hand-set weight", "Learned weight", "Change"].forEach((h) => hr.append(ui.el("th", {}, h)));
    tbl.append(hr);
    for (const e of p.explanation) {
      const tr = ui.el("tr");
      [e.label, String(e.hand_set), String(e.learned), e.change].forEach((v) => tr.append(ui.el("td", {}, v)));
      tbl.append(tr);
    }
    const wrap = ui.el("div", { class: "table-wrap" });
    wrap.append(tbl);
    box.append(wrap);

    const holder = ui.el("div", { style: "height:200px;margin-top:12px" });
    const canvas = ui.el("canvas", { id: "learned-curve", "aria-label": "Food saved on each practice day" });
    holder.append(canvas);
    box.append(holder);
    if (window.Chart) {
      if (chart) chart.destroy();
      const textColor = getComputedStyle(document.body).color;
      chart = new Chart(canvas, {
        type: "line",
        data: { labels: p.training_curve.map((_, i) => i + 1),
                datasets: [{ label: "Food saved on each practice day (%)", data: p.training_curve, borderColor: "#1f6feb",
                             pointRadius: 0, borderWidth: 1.5 }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false,
                   plugins: { legend: { labels: { color: textColor } } },
                   scales: { x: { ticks: { color: textColor, maxTicksLimit: 10 } }, y: { ticks: { color: textColor } } } },
      });
    }
  }

  window.FRSLearned = {
    show(helpers) {
      ui = helpers;
      return ui.api("/learned-dispatch").then(render).catch((e) => { $("learned-box").textContent = e.message; });
    },
  };
})();
