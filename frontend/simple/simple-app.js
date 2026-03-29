/**
 * simple-app.js
 * Teacher-friendly dashboard for Texas ERCOT Smart Energy Optimizer
 * Shows only the most important information in easy-to-understand format
 */

const API_BASE = window.location.origin;
let simpleChart = null;
let activeModel = "prophet";
let fallbackNotified = false;

// Load on start
window.addEventListener("DOMContentLoaded", () => {
  loadData();
  loadDataStatus();
  setInterval(loadData, 60_000);  // refresh every minute
});

// Main data load
async function loadData() {
  try {
    let data;

    try {
      data = await fetchForecast(activeModel, 24);
    } catch (err) {
      const message = String(err.message || "");
      const canFallback =
        activeModel === "lstm" &&
        (
          message.includes("tensorflow") ||
          message.includes("not been trained") ||
          err.status >= 500
        );

      if (!canFallback) throw err;

      activeModel = "prophet";
      data = await fetchForecast(activeModel, 24);

      if (!fallbackNotified) {
        showToast("LSTM unavailable. Switched to Prophet forecast.", "warning");
        fallbackNotified = true;
      }
    }

    updateDashboard(data.forecast, data.threshold_mw);
    renderSimpleChart(data.forecast, data.threshold_mw);
    updateCurrentStatus();

  } catch (err) {
    showToast("Error loading data: " + err.message, "error");
    console.error(err);
  }
}

async function fetchForecast(model, hours) {
  const res = await fetch(`${API_BASE}/api/forecast?model=${model}&hours=${hours}`);
  if (!res.ok) {
    const detail = await res.text();
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// Update main metrics
function updateDashboard(forecast, threshold) {
  const peakItem = forecast.reduce((a, b) => a.predicted_mw > b.predicted_mw ? a : b, forecast[0]);
  const alertCount = forecast.filter(f => f.predicted_mw >= threshold).length;
  const hasAlerts = alertCount > 0;

  // Metrics
  document.getElementById("metric-peak").textContent = `${peakItem.predicted_mw.toFixed(1)} MW`;
  document.getElementById("metric-alerts").textContent = alertCount;
  document.getElementById("metric-threshold").textContent = `${threshold} MW`;

  // Status bar
  const statusBar = document.getElementById("status-bar");
  const statusIcon = document.getElementById("status-icon");
  const statusTitle = document.getElementById("status-title");
  const statusText = document.getElementById("status-text");

  if (hasAlerts) {
    statusBar.className = "status-bar status-alert";
    statusIcon.className = "status-icon fas fa-exclamation-circle";
    statusTitle.textContent = "Demand Alert";
    statusText.textContent = `${alertCount} hour(s) may exceed safe threshold (${threshold} MW)`;

    // Show alert card
    const alertCard = document.getElementById("alert-card");
    const peakTime = new Date(peakItem.timestamp);
    document.getElementById("alert-time").textContent =
      `Peak demand around ${peakTime.toLocaleTimeString([], {hour: "2-digit", minute:"2-digit"})}`;
    alertCard.style.display = "";
  } else {
    statusBar.className = "status-bar status-ok";
    statusIcon.className = "status-icon fas fa-check-circle";
    statusTitle.textContent = "Energy Supply Safe";
    statusText.textContent = "All forecast values are within normal range";
    document.getElementById("alert-card").style.display = "none";
  }

  // Populate the hourly details table using already-fetched data
  populateTable(forecast, threshold);
}

// Simple chart
function renderSimpleChart(forecast, threshold) {
  const labels = forecast.map(f => {
    const d = new Date(f.timestamp);
    return d.toLocaleTimeString([], {hour: "2-digit", minute:"2-digit"});
  });
  const predictedMw = forecast.map(f => f.predicted_mw);
  const thresholdLine = forecast.map(() => threshold);

  const ctx = document.getElementById("simple-chart").getContext("2d");
  if (simpleChart) simpleChart.destroy();

  simpleChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Electricity Demand (MW)",
          data: predictedMw,
          borderColor: "#2563eb",
          backgroundColor: "rgba(37, 99, 235, 0.1)",
          borderWidth: 3,
          pointRadius: 4,
          pointBackgroundColor: "#2563eb",
          fill: false,
          tension: 0.3,
        },
        {
          label: "Safe Threshold",
          data: thresholdLine,
          borderColor: "#dc2626",
          borderWidth: 2,
          borderDash: [8, 4],
          pointRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "top",
          labels: {
            font: { size: 14, weight: "bold" },
            padding: 20,
            boxHeight: 8,
          },
        },
        tooltip: {
          backgroundColor: "rgba(0, 0, 0, 0.8)",
          padding: 12,
          titleFont: { size: 14 },
          bodyFont: { size: 13 },
          displayColors: true,
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} MW`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            font: { size: 12 },
            maxTicksLimit: 12,
          },
          grid: { color: "rgba(0, 0, 0, 0.05)" },
        },
        y: {
          title: {
            display: true,
            text: "MW (Megawatts)",
            font: { size: 13, weight: "bold" },
          },
          ticks: {
            font: { size: 12 },
            callback: (v) => v.toFixed(0),
          },
          grid: { color: "rgba(0, 0, 0, 0.08)" },
        },
      },
    },
  });
}

// Load data status
async function loadDataStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/eia/status`);
    const data = await res.json();

    if (data.status !== "no_data") {
      document.getElementById("info-source").textContent =
        data.source === "EIA ERCO" ? "Real Energy Data (EIA)" :  "Smart Synthetic Data";
      document.getElementById("info-rows").textContent = data.rows.toLocaleString();
    }
  } catch (_) {}
}

// Update current time and weather
function updateCurrentStatus() {
  const now = new Date();
  document.getElementById("current-time").textContent =
    now.toLocaleTimeString([], {hour: "2-digit", minute:"2-digit", second:"2-digit"});

  try {
    fetch(`${API_BASE}/api/weather`)
      .then(r => r.json())
      .then(data => {
        document.getElementById("weather-temp").textContent = `${Math.round(data.temperature)}°C`;
      });
  } catch (_) {}
}

// Load/show hourly details
function loadDetails() {
  const section = document.getElementById("details-section");
  if (section.style.display === "none") {
    section.style.display = "";
    document.getElementById("details-btn").innerHTML = '<i class="fas fa-list"></i> Hide Hourly Details';
  } else {
    section.style.display = "none";
    document.getElementById("details-btn").innerHTML = '<i class="fas fa-list"></i> Show Hourly Details';
  }
}

// Populate hourly table (called after loadData)
function populateTable(forecast, threshold) {
  const tbody = document.getElementById("table-body");
  const rows = forecast.map(f => {
    const d = new Date(f.timestamp);
    const time = d.toLocaleTimeString([], {hour: "2-digit", minute:"2-digit"});
    const isSafe = f.predicted_mw < threshold;
    const status = isSafe
      ? '<i class="fas fa-check-circle"></i> Safe'
      : '<i class="fas fa-exclamation-triangle"></i> Alert';
    const statusClass = isSafe ? "status-safe" : "status-alert";

    return `
      <tr class="detail-row">
        <td>${time}</td>
        <td class="metric-cell">${f.predicted_mw.toFixed(2)} MW</td>
        <td><span class="badge ${statusClass}">${status}</span></td>
      </tr>
    `;
  }).join("");

  tbody.innerHTML = rows || `<tr><td colspan="3">No data</td></tr>`;
}

// Hook into loadData to also populate table — removed (populateTable is now
// called directly from updateDashboard to avoid a redundant API request).

// Toast notification
function showToast(msg, type = "info") {
  const toast = document.getElementById("toast");
  toast.textContent = msg;
  toast.className = "toast";
  if (type === "error") toast.style.background = "#dc2626";
  else if (type === "success") toast.style.background = "#16a34a";
  else toast.style.background = "#1a1d23";
  setTimeout(() => toast.classList.add("hidden"), 3500);
}
