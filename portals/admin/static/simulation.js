// Host portal, Phase 8: run the simulator and compare strategies (charts, table, CSV download).
(function () {
  const $ = (id) => document.getElementById(id);
  const FIELDS = [
    ["restaurants", "Restaurants", 40, 1], ["ngos", "NGOs", 15, 1], ["posts_per_day", "Food posts per day", 80, 1],
    ["days", "Days per city", 5, 1], ["runs", "Cities to average over", 3, 1], ["seed", "Seed (same seed = same city)", 1, 1],
    ["availability", "Agents on duty (%)", 45, 100], ["accept_prob", "Agents say yes (%)", 75, 100],
    ["flaky_share", "Unreliable NGOs (%)", 25, 100], ["backup_share", "Backup collectors (%)", 20, 100],
  ];
  const CHARTS = [["food_saved_pct", "Food saved (%)"], ["minutes_to_pickup", "Time to pickup (min)"],
                  ["km_per_100_portions", "km per 100 portions"], ["giveback_rate_pct", "Give-back rate (%)"]];
  const COLORS = ["#8c959f", "#1f6feb", "#1a7f37", "#bf8700"];
  let charts = [];
  let last = null;
  let ui = null;

  function form() {
    const f = $("sim-form");
    if (f.dataset.built) return;
    f.dataset.built = "1";
    for (const [name, label, value] of FIELDS) {
      const w = document.createElement("div");
      const l = document.createElement("label");
      l.htmlFor = "sim-" + name;
      l.textContent = label;
      const i = document.createElement("input");
      Object.assign(i, { id: "sim-" + name, name, type: "number", value: String(value), min: "0", step: "1" });
      w.append(l, i);
      f.append(w);
    }
  }

  function settings() {
    const out = {};
    for (const [name, , , scale] of FIELDS) {
      const v = Number($("sim-" + name).value);
      out[name] = scale === 100 ? v / 100 : Math.round(v);
    }
    return out;
  }

  function table(result) {
    const keys = Object.keys(result.metrics);
    const names = Object.keys(result.summary);
    const t = $("sim-table");
    t.replaceChildren();
    const head = document.createElement("tr");
    head.append(ui.el("th", {}, "Measure"), ...names.map((n) => ui.el("th", {}, result.summary[n].label)));
    t.append(head);
    for (const k of keys) {
      const m = result.metrics[k];
      const vals = names.map((n) => result.summary[n].metrics[k].mean);
      const valid = vals.filter((v) => v !== null);
      const best = valid.length ? (m.higher_is_better ? Math.max(...valid) : Math.min(...valid)) : null;
      const tr = document.createElement("tr");
      tr.append(ui.el("td", { title: m.unit }, `${m.label} (${m.unit})`));
      names.forEach((n, i) => {
        const s = result.summary[n].metrics[k];
        const td = ui.el("td", {}, s.mean === null ? "—" : `${s.mean}${s.spread ? " ± " + s.spread : ""}`);
        if (s.mean !== null && s.mean === best && valid.length > 1) td.style.fontWeight = "600";
        tr.append(td);
      });
      t.append(tr);
    }
  }

  function drawCharts(result) {
    charts.forEach((c) => c.destroy());
    charts = [];
    if (!window.Chart) { $("sim-status").textContent = "Charts need an internet connection; the table below has all results."; return; }
    const names = Object.keys(result.summary);
    const textColor = getComputedStyle(document.body).color;
    for (const [i, [key, title]] of CHARTS.entries()) {
      const canvas = $("sim-chart-" + i);
      charts.push(new Chart(canvas, {
        type: "bar",
        data: { labels: names.map((n) => result.summary[n].label),
                datasets: [{ label: title, data: names.map((n) => result.summary[n].metrics[key].mean),
                             backgroundColor: names.map((_, j) => COLORS[j % COLORS.length]) }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false,
                   plugins: { legend: { display: false }, title: { display: true, text: title, color: textColor } },
                   scales: { x: { ticks: { color: textColor } }, y: { beginAtZero: true, ticks: { color: textColor } } } },
      }));
    }
  }

  function csv(result) {
    const keys = Object.keys(result.metrics);
    const lines = [["run", "seed", "strategy", ...keys].join(",")];
    for (const r of result.runs) lines.push([r.run, r.seed, r.strategy, ...keys.map((k) => r[k] ?? "")].join(","));
    return lines.join("\n");
  }

  async function run() {
    const btn = $("sim-run");
    btn.disabled = true;
    $("sim-status").textContent = "Running the simulation… (up to about half a minute)";
    $("sim-status").className = "msg";
    try {
      last = await ui.api("/simulation/run", { method: "POST", body: JSON.stringify(settings()) });
      $("sim-results").classList.remove("hidden");
      table(last);
      drawCharts(last);
      const c = last.config;
      $("sim-status").textContent = `Done: ${c.runs} cities × ${c.days} days × ${c.posts_per_day} food posts per day, for each strategy.`;
      $("sim-status").className = "msg ok";
    } catch (e) {
      $("sim-status").textContent = e.message;
      $("sim-status").className = "msg err";
    }
    btn.disabled = false;
  }

  window.FRSSimulation = {
    show(helpers) {
      ui = helpers;
      form();
      const btn = $("sim-run");
      if (!btn.dataset.wired) {
        btn.dataset.wired = "1";
        btn.addEventListener("click", run);
        $("sim-csv").addEventListener("click", () => {
          if (!last) return;
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([csv(last)], { type: "text/csv" }));
          a.download = `simulation_seed${last.config.seed}.csv`;
          a.click();
          URL.revokeObjectURL(a.href);
        });
      }
    },
  };
})();
