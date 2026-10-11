"use strict";

const $ = (sel) => document.querySelector(sel);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const fmt = (v, d = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

let programs = {};
let sensorNames = ["arriba", "medio", "abajo"];
let rangeHours = 24;
let settingsLoaded = false;
const charts = {};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
    throw new Error(detail || res.statusText);
  }
  return data;
}

function setPill(el, on, text) {
  el.classList.toggle("on", !!on);
  if (text) el.textContent = text;
}

// --- Status ---------------------------------------------------------------

async function refreshStatus() {
  const s = await api("/api/status");
  const r = s.reading || {};
  const online = $("#online");
  online.textContent = s.online ? "en línea" : "sin conexión";
  online.className = "pill " + (s.online ? "on" : "warn");

  $("#t").textContent = fmt(r.t);
  $("#rh").textContent = fmt(r.rh);
  $("#t-set").textContent = fmt(s.config.temp_c);
  $("#rh-set").textContent = fmt(s.config.rh, 0);

  setPill($("#out-comp"), r.compressor, r.dehumidifying ? "Nevera (secando)" : "Nevera");
  setPill($("#out-hum"), r.humidifier);
  setPill($("#out-fan"), r.fan > 0, `Ventilador ${r.fan ?? 0}%`);
  $("#duty").textContent =
    `Últimas 24 h: nevera ${s.duty_24h.compressor}% · humidificador ${s.duty_24h.humidifier}% del tiempo`;

  if (s.device?.sensor_names?.every(Boolean)) sensorNames = s.device.sensor_names;
  $("#sensors").innerHTML =
    "<tr><td></td><td>°C</td><td>%HR</td></tr>" +
    sensorNames
      .map((n, i) => `<tr><td>${esc(n)}</td><td>${fmt(r[`s${i}_t`])}</td><td>${fmt(r[`s${i}_rh`])}</td></tr>`)
      .join("");
  const alarms = (r.alarms || "").split(",").filter(Boolean);
  $("#device").innerHTML =
    (s.device ? `WiFi ${s.device.rssi} dBm · ${s.config_synced ? "config sincronizada" : "config pendiente"}` : "") +
    (alarms.length ? `<br><span class="pill warn">${alarms.join(" ")}</span>` : "");

  const p = s.program;
  $("#phase").textContent = p
    ? `${p.program_name} · ${p.phase_name} · día ${Math.floor(p.day)} de ${p.total_days}`
    : "Modo manual";

  if (s.latest_photo) {
    const url = `/photos/${s.latest_photo.path}`;
    if ($("#photo").dataset.src !== url) {
      $("#photo").src = $("#photo").dataset.src = url;
      $("#photo-link").href = url;
      $("#photo-ts").textContent = fmtTime(s.latest_photo.ts);
      refreshPhotos();
    }
  }

  $("#events").innerHTML = s.events.length
    ? s.events.map((e) => `<li>${fmtTime(e.ts)} <span class="kind">${esc(e.kind)}</span> ${esc(e.message)}</li>`).join("")
    : "<li class='muted'>Sin avisos</li>";
}

// --- Charts ---------------------------------------------------------------

function lineChart(canvas, unit, extra = {}) {
  return new Chart(canvas, {
    type: "line",
    data: { datasets: [] },
    options: {
      animation: false,
      maintainAspectRatio: false,
      parsing: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.5 } },
      scales: {
        x: {
          type: "linear",
          ticks: { color: css("--muted"), maxTicksLimit: 8, callback: (v) => fmtTime(v) },
          grid: { color: css("--border") },
        },
        y: { ticks: { color: css("--muted"), callback: (v) => `${v}${unit}` }, grid: { color: css("--border") }, ...extra.y },
      },
      plugins: {
        legend: { labels: { color: css("--text"), boxWidth: 12 } },
        tooltip: { callbacks: { title: (items) => fmtTime(items[0].parsed.x) } },
      },
    },
  });
}

function series(rows, key, label, color, opts = {}) {
  return {
    label,
    data: rows.filter((r) => r[key] !== null).map((r) => ({ x: r.ts, y: r[key] })),
    borderColor: color,
    backgroundColor: color,
    ...opts,
  };
}

function setpointLine(rows, value, label) {
  if (!rows.length) return null;
  return {
    label,
    data: [{ x: rows[0].ts, y: value }, { x: rows[rows.length - 1].ts, y: value }],
    borderColor: css("--muted"),
    borderDash: [6, 4],
  };
}

async function refreshHistory() {
  const [{ rows }, status] = await Promise.all([api(`/api/history?hours=${rangeHours}`), api("/api/status")]);
  const colors = [css("--s0"), css("--s1"), css("--s2")];
  charts.t ??= lineChart($("#chart-t"), "°");
  charts.rh ??= lineChart($("#chart-rh"), "%");
  charts.out ??= lineChart($("#chart-out"), "%", { y: { min: 0, max: 100 } });

  charts.t.data.datasets = [
    series(rows, "t", "Media", css("--avg"), { borderWidth: 2.5 }),
    ...sensorNames.map((n, i) => series(rows, `s${i}_t`, n, colors[i])),
    setpointLine(rows, status.config.temp_c, "Consigna"),
  ].filter(Boolean);
  charts.rh.data.datasets = [
    series(rows, "rh", "Media", css("--avg"), { borderWidth: 2.5 }),
    ...sensorNames.map((n, i) => series(rows, `s${i}_rh`, n, colors[i])),
    setpointLine(rows, status.config.rh, "Consigna"),
  ].filter(Boolean);
  charts.out.data.datasets = [
    series(rows, "compressor", "Nevera (% tiempo)", css("--s2"), { stepped: true }),
    series(rows, "humidifier", "Humidificador (% tiempo)", css("--s1"), { stepped: true }),
    series(rows, "fan", "Ventilador (%)", css("--muted"), { stepped: true }),
  ];
  const now = Date.now() / 1000;
  Object.values(charts).forEach((c) => {
    c.options.scales.x.min = now - rangeHours * 3600;
    c.options.scales.x.max = now;
    c.update();
  });
}

// --- Photos / timelapse ------------------------------------------------------

async function refreshPhotos() {
  const photos = await api("/api/photos?limit=24");
  $("#strip").innerHTML = photos
    .map((p) => `<img src="/photos/${p.path}" title="${fmtTime(p.ts)}" data-ts="${p.ts}" loading="lazy">`)
    .join("");
}

$("#strip").addEventListener("click", (e) => {
  if (e.target.tagName !== "IMG") return;
  $("#photo").src = e.target.src;
  $("#photo-link").href = e.target.src;
  $("#photo-ts").textContent = fmtTime(e.target.dataset.ts);
});

async function refreshTimelapse() {
  const t = await api("/api/timelapse");
  const st = t.status;
  $("#tl-status").textContent = st.running
    ? "Generando timelapse…"
    : st.error ? `Error: ${st.error}` : "";
  $("#videos").innerHTML = t.videos
    .map((v) => `<li><a href="/videos/${v.name}" target="_blank">${v.name}</a> (${(v.size / 1e6).toFixed(1)} MB)</li>`)
    .join("");
  if (st.running) setTimeout(refreshTimelapse, 3000);
}

$("#tl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/timelapse", { method: "POST", body: { days: +f.get("days"), fps: +f.get("fps") } });
  } catch (err) {
    $("#tl-status").textContent = err.message;
    return;
  }
  refreshTimelapse();
});

// --- Hams ---------------------------------------------------------------------

async function refreshHams() {
  const hams = await api("/api/hams");
  $("#hams").innerHTML = hams.length
    ? hams
        .map((h) => {
          const pct = Math.min(100, (100 * h.loss_pct) / h.target_loss_pct);
          const last = h.weights.length ? fmtTime(h.weights[h.weights.length - 1].ts) : "sin pesadas";
          return `<div class="ham">
            <strong>${esc(h.name)}</strong> ${h.done ? '<span class="pill on">listo</span>' : ""}
            <div class="small">${h.current_weight_g} g de ${h.initial_weight_g} g ·
              merma <strong>${h.loss_pct}%</strong> / objetivo ${h.target_loss_pct}% (${h.target_weight_g} g)
              · ${last}</div>
            <div class="bar"><div style="width:${pct}%"></div></div>
            <form data-ham="${h.id}"><input name="w" type="number" min="1" placeholder="peso (g)" required><button>Anotar</button></form>
          </div>`;
        })
        .join("")
    : "<p class='muted'>Añade tus piezas para seguir la merma (pérdida de peso).</p>";
}

$("#hams").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = e.target.dataset.ham;
  await api(`/api/hams/${id}/weights`, { method: "POST", body: { weight_g: +e.target.w.value } });
  refreshHams();
});

$("#ham-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  await api("/api/hams", {
    method: "POST",
    body: { name: f.get("name"), initial_weight_g: +f.get("initial_weight_g"), target_loss_pct: +f.get("target_loss_pct") },
  });
  e.target.reset();
  refreshHams();
});

// --- Settings -------------------------------------------------------------------

function renderProgramPhases() {
  const p = programs[$("#program-select").value];
  $("#program-phases").innerHTML = p
    ? `${esc(p.description)}<br>` +
      p.phases.map((ph) => `${esc(ph.name)}: ${ph.days} d · ${ph.temp_c} °C · ${ph.rh}%`).join("<br>")
    : "";
}

function updateModeNote() {
  const program = document.querySelector("input[name=mode]:checked")?.value === "program";
  $("#manual-note").textContent = program
    ? "(en modo programa, temperatura, humedad y ventilador base los marca el programa)"
    : "";
}

async function loadSettings() {
  programs = await api("/api/programs");
  $("#program-select").innerHTML = Object.entries(programs)
    .map(([k, p]) => `<option value="${esc(k)}">${esc(p.name)} (${p.total_days} d)</option>`)
    .join("");

  const s = await api("/api/settings");
  const form = $("#settings");
  form.querySelector(`input[name=mode][value=${s.mode}]`).checked = true;
  if (s.program) form.program.value = s.program;
  form.program_start.value = s.program_start || new Date().toISOString().slice(0, 10);
  for (const el of form.querySelectorAll("[data-sp]")) {
    const v = s.setpoints[el.dataset.sp];
    if (el.type === "checkbox") el.checked = v;
    else el.value = v;
  }
  $("#offsets").innerHTML = s.setpoints.offsets
    .map(
      (o, i) => `<label>${esc(sensorNames[i])}: °C <input data-off="${i}" data-k="t" type="number" step="0.1" value="${o.t}">
        %HR <input data-off="${i}" data-k="rh" type="number" step="0.5" value="${o.rh}"></label>`
    )
    .join("");
  renderProgramPhases();
  updateModeNote();
  settingsLoaded = true;
}

$("#program-select").addEventListener("change", renderProgramPhases);
document.querySelectorAll("input[name=mode]").forEach((el) => el.addEventListener("change", updateModeNote));

$("#settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!settingsLoaded) return;
  const form = e.target;
  const setpoints = {};
  for (const el of form.querySelectorAll("[data-sp]")) {
    setpoints[el.dataset.sp] =
      el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  setpoints.offsets = [0, 1, 2].map((i) => ({
    t: Number(form.querySelector(`[data-off="${i}"][data-k=t]`).value),
    rh: Number(form.querySelector(`[data-off="${i}"][data-k=rh]`).value),
  }));
  const body = {
    mode: form.querySelector("input[name=mode]:checked").value,
    program: form.program.value || null,
    program_start: form.program_start.value || null,
    setpoints,
  };
  const msg = $("#save-msg");
  try {
    await api("/api/settings", { method: "PUT", body });
    msg.textContent = "Guardado. La ESP32 lo aplicará en el próximo envío (≤30 s).";
    refreshStatus();
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
  }
});

$("#range").addEventListener("click", (e) => {
  if (!e.target.dataset.h) return;
  rangeHours = +e.target.dataset.h;
  document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("on", b === e.target));
  refreshHistory();
});

// --- Boot -------------------------------------------------------------------------

async function boot() {
  await refreshStatus().catch(console.error);
  await Promise.all([loadSettings(), refreshHistory(), refreshPhotos(), refreshHams(), refreshTimelapse()]).catch(
    console.error
  );
  setInterval(() => refreshStatus().catch(console.error), 15000);
  setInterval(() => refreshHistory().catch(console.error), 60000);
}

boot();
