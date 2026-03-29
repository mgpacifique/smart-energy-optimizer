/**
 * app.js
 * Dashboard logic for Texas ERCOT Smart Energy Optimizer
 * Fetches forecast + alert data from the FastAPI backend and renders them.
 */

const API_BASE = window.location.origin;

let forecastData  = [];   // raw forecast array from API
let sortKey       = "timestamp";
let sortAsc       = true;
let forecastChart = null;

// ── On load ──────────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
  loadForecast();
  loadAlerts();
  loadWeather();
  loadDataStatus();
  setInterval(loadForecast, 60_000);
  setInterval(loadAlerts,   30_000);
});

// ── EIA Sync ──────────────────────────────────────────────────────────────────
async function syncEIA() {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  showToast("Fetching ERCO data from EIA — this may take 30–60 seconds.", "info");

  try {
    const res = await fetch(`${API_BASE}/api/eia/sync`, { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    showToast(
      `EIA sync complete — ${data.rows.toLocaleString()} rows, ` +
      `load ${data.load_mw.min}–${data.load_mw.max} MW`, "success"
    );
    await loadDataStatus();
    await loadForecast();
  } catch (err) {
    showToast("EIA sync failed: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "⇙ Sync EIA data";
  }
}

// ── Data status ───────────────────────────────────────────────────────────────
async function loadDataStatus() {
  try {
    const res  = await fetch(`${API_BASE}/api/eia/status`);
    const data = await res.json();

    const sourceEl = document.getElementById("stat-source");
    const rowsEl   = document.getElementById("stat-rows");

    if (data.status === "no_data") {
      if (sourceEl) sourceEl.textContent = "No data yet";
      if (rowsEl)   rowsEl.textContent   = "—";
      return;
    }

    if (sourceEl) sourceEl.textContent = data.source === "EIA ERCO"
      ? "EIA ERCO (real)" : "Synthetic fallback";
    if (rowsEl) rowsEl.textContent = data.rows.toLocaleString();

  } catch (_) {}
}

// ── Fetch forecast ───────────────────────────────────────────────────────────
async function loadForecast() {
  const model  = document.getElementById("model-select").value;
  const hours  = document.getElementById("hours-select").value;
  document.getElementById("stat-model").textContent = model === "prophet" ? "Prophet" : "LSTM";

  try {
    const res  = await fetch(`${API_BASE}/api/forecast?model=${model}&hours=${hours}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    forecastData = data.forecast;
    document.getElementById("stat-threshold").textContent = `${data.threshold_mw} MW`;
    document.getElementById("last-updated").textContent =
      "Updated " + new Date().toLocaleTimeString();

    updateStatCards(forecastData, data.threshold_mw);
    renderChart(forecastData, data.threshold_mw);
    renderTable(forecastData);
    updateStatusBar(forecastData, data.threshold_mw);

  } catch (err) {
    showToast("Forecast error: " + err.message, "error");
    console.error(err);
  }
}

// ── Train model ───────────────────────────────────────────────────────────────
async function trainModel() {
  const model = document.getElementById("model-select").value;
  const btn   = document.getElementById("train-btn");
  btn.disabled = true;
  btn.textContent = "Training…";
  showToast(`Training ${model} model… this may take a minute.`, "info");

  try {
    const res = await fetch(`${API_BASE}/api/train`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ model }),
    });
    if (!res.ok) throw new Error(await res.text());
    showToast(`${model} model trained successfully!`, "success");
    await loadForecast();
  } catch (err) {
    showToast("Training failed: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ Retrain";
  }
}

// ── Stat cards ────────────────────────────────────────────────────────────────
function updateStatCards(forecast, threshold) {
  const peakItem  = forecast.reduce((a, b) => a.predicted_mw > b.predicted_mw ? a : b, forecast[0]);
  const alertCount = forecast.filter(f => f.predicted_mw >= threshold).length;

  document.getElementById("stat-peak").textContent   = `${peakItem.predicted_mw.toFixed(1)} MW`;
  document.getElementById("stat-alerts").textContent = alertCount;
}

// ── Status bar ────────────────────────────────────────────────────────────────
function updateStatusBar(forecast, threshold) {
  const bar     = document.getElementById("status-bar");
  const text    = document.getElementById("status-text");
  const icon    = document.getElementById("status-icon");
  const alertHours = forecast.filter(f => f.predicted_mw >= threshold);

  bar.className = "status-bar";
  if (alertHours.length === 0) {
    bar.classList.add("status-ok");
    text.textContent = "All forecast values within safe threshold";
    icon.className = "fas fa-check-circle";
  } else {
    bar.classList.add("status-alert");
    text.textContent = `ALERT \u2014 ${alertHours.length} hour(s) forecast above ${threshold} MW`;
    icon.className = "fas fa-exclamation-circle";
    buildShedSchedule(alertHours, threshold);
  }
}

// ── Chart ─────────────────────────────────────────────────────────────────────
function renderChart(forecast, threshold) {
  const labels      = forecast.map(f => formatTime(f.timestamp));
  const predicted   = forecast.map(f => f.predicted_mw);
  const lower       = forecast.map(f => f.lower_mw);
  const upper       = forecast.map(f => f.upper_mw);
  const threshLine  = forecast.map(() => threshold);

  const ctx = document.getElementById("forecast-chart").getContext("2d");
  if (forecastChart) forecastChart.destroy();

  forecastChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label:           "Predicted load (MW)",
          data:            predicted,
          borderColor:     "#2563eb",
          backgroundColor: "rgba(37,99,235,.08)",
          borderWidth:     2,
          pointRadius:     2,
          fill:            false,
          tension:         0.3,
        },
        {
          label:           "Upper bound",
          data:            upper,
          borderColor:     "rgba(37,99,235,.25)",
          borderWidth:     1,
          borderDash:      [4, 4],
          pointRadius:     0,
          fill:            "+1",
          backgroundColor: "rgba(37,99,235,.06)",
          tension:         0.3,
        },
        {
          label:           "Lower bound",
          data:            lower,
          borderColor:     "rgba(37,99,235,.25)",
          borderWidth:     1,
          borderDash:      [4, 4],
          pointRadius:     0,
          fill:            false,
          tension:         0.3,
        },
        {
          label:           "Alert threshold",
          data:            threshLine,
          borderColor:     "#dc2626",
          borderWidth:     1.5,
          borderDash:      [6, 3],
          pointRadius:     0,
          fill:            false,
        },
      ],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} MW`,
          },
        },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 12, font: { size: 11 } }, grid: { color: "#f0f0f0" } },
        y: {
          title: { display: true, text: "Load (MW)", font: { size: 11 } },
          grid:  { color: "#f0f0f0" },
          ticks: { font: { size: 11 }, callback: v => v.toFixed(0) + " MW" },
        },
      },
    },
  });

  document.getElementById("chart-subtitle").textContent =
    `Texas ERCOT · ${forecast.length} hours · MW`;
}

// ── Table ─────────────────────────────────────────────────────────────────────
function renderTable(data) {
  const filter    = document.getElementById("filter-alerts").value;
  const threshold = parseFloat(document.getElementById("stat-threshold").textContent);

  let rows = [...data];
  if (filter === "alerts") rows = rows.filter(r => r.predicted_mw >= threshold);

  // Sort
  rows.sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey];
    if (typeof va === "string") return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return sortAsc ? va - vb : vb - va;
  });

  const tbody = document.getElementById("table-body");
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-row">No data to display.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(r => {
    const isAlert = r.predicted_mw >= threshold;
    return `
      <tr class="${isAlert ? "alert-row" : ""}">
        <td>${formatTime(r.timestamp)}</td>
        <td><strong>${r.predicted_mw.toFixed(2)}</strong></td>
        <td>${r.lower_mw.toFixed(2)}</td>
        <td>${r.upper_mw.toFixed(2)}</td>
        <td><span class="badge ${isAlert ? "badge-alert" : "badge-ok"}">
          ${isAlert ? "⚠ Alert" : "✓ Normal"}
        </span></td>
      </tr>`;
  }).join("");
}

function sortTable(key) {
  if (sortKey === key) sortAsc = !sortAsc;
  else { sortKey = key; sortAsc = true; }
  renderTable(forecastData);
}

function filterTable() {
  const q     = document.getElementById("table-search").value.toLowerCase();
  const rows  = document.querySelectorAll("#table-body tr");
  rows.forEach(row => {
    row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none";
  });
}

// ── Shed schedule ─────────────────────────────────────────────────────────────
function buildShedSchedule(alertHours, threshold) {
  const section = document.getElementById("shed-section");
  const wrap    = document.getElementById("shed-table-wrap");
  section.style.display = "";

  // Fetch the latest full payload from backend
  const model = document.getElementById("model-select").value;
  fetch(`${API_BASE}/api/forecast?model=${model}&hours=24`)
    .then(r => r.json())
    .then(data => {
      const peak = data.forecast.reduce((a, b) => a.predicted_mw > b.predicted_mw ? a : b);
      // Build schedule locally (mirrors server logic)
      const zones = [
        "Houston Zone (ERCOT-H)",
        "North Zone (ERCOT-N)",
        "West Zone (ERCOT-W)",
        "South Zone (ERCOT-S)",
        "Far West Zone (ERCOT-FW)",
      ];
      const excess       = Math.max(0, peak.predicted_mw - threshold);
      const loadPerZone  = peak.predicted_mw / zones.length;
      let remaining      = excess;
      let schedule       = [];

      for (const zone of zones) {
        if (remaining <= 0) break;
        const zLoad   = Math.min(loadPerZone, remaining);
        const durHrs  = Math.max(0.5, parseFloat((zLoad / loadPerZone * 2).toFixed(1)));
        schedule.push({ zone, duration_hrs: durHrs, load_mw: zLoad.toFixed(2) });
        remaining -= zLoad;
      }

      wrap.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Zone</th>
              <th>Duration</th>
              <th>Est. load shed</th>
            </tr>
          </thead>
          <tbody>
            ${schedule.map(s => `
              <tr>
                <td>${s.zone}</td>
                <td>${s.duration_hrs}h</td>
                <td>${parseFloat(s.load_mw).toFixed(2)} MW</td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    })
    .catch(() => {});
}

// ── Alerts ────────────────────────────────────────────────────────────────────
async function loadAlerts() {
  try {
    const res  = await fetch(`${API_BASE}/api/alerts`);
    const data = await res.json();
    const list = document.getElementById("alert-list");

    if (data.alerts.length === 0) {
      list.innerHTML = `<p class="muted-text">No alerts recorded yet.</p>`;
      return;
    }

    list.innerHTML = data.alerts.map(a => `
      <div class="alert-item">
        <div class="alert-dot"></div>
        <div>
          <div class="alert-title">Peak ${a.peak_mw.toFixed(2)} MW — ${a.model.toUpperCase()}</div>
          <div class="alert-meta">${new Date(a.timestamp).toLocaleString()}</div>
        </div>
      </div>`).join("");

  } catch (_) {}
}

// ── Weather ───────────────────────────────────────────────────────────────────
async function loadWeather() {
  try {
    const res  = await fetch(`${API_BASE}/api/weather`);
    const data = await res.json();
    document.getElementById("weather-temp").textContent  = `${data.temperature}°C`;
    document.getElementById("weather-label").textContent = "Dallas, TX";
  } catch (_) {}
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function showToast(msg, type = "info") {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.className = "toast";
  if (type === "error")   toast.style.background = "#dc2626";
  else if (type === "success") toast.style.background = "#16a34a";
  else                    toast.style.background = "#1a1d23";
  setTimeout(() => toast.classList.add("hidden"), 4000);
}
