const API_BASE = "http://54.165.62.144";
let evalChart = null;

window.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("reload-eval");
  const runBtn = document.getElementById("run-eval");
  if (btn) btn.addEventListener("click", loadEvaluation);
  if (runBtn) runBtn.addEventListener("click", runEvaluation);
  loadEvaluation();
});

async function loadEvaluation() {
  try {
    const res = await fetch(`${API_BASE}/api/evaluation/summary`);
    if (!res.ok) {
      throw new Error(await res.text());
    }

    const payload = await res.json();
    renderRecommendation(payload);
    renderTable(payload);
    renderChart(payload);
    renderInterpretation(payload);
  } catch (err) {
    showToast("Could not load evaluation summary: " + err.message, "error");
    const tbody = document.getElementById("eval-table-body");
    if (tbody) {
      tbody.innerHTML = '<tr><td colspan="6" class="loading">No evaluation data found. Run python evaluate.py in backend first.</td></tr>';
    }
  }
}

async function runEvaluation() {
  const runBtn = document.getElementById("run-eval");
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Running...';
  }

  try {
    const res = await fetch(`${API_BASE}/api/evaluation/run`, { method: "POST" });
    if (!res.ok) {
      throw new Error(await res.text());
    }

    const payload = await res.json();
    const summary = payload.summary;
    renderRecommendation(summary);
    renderTable(summary);
    renderChart(summary);
    renderInterpretation(summary);
    showToast("Evaluation completed and results updated.", "success");
  } catch (err) {
    showToast("Evaluation failed: " + err.message, "error");
  } finally {
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = '<i class="fas fa-play"></i> Run Evaluation Now';
    }
  }
}

function renderRecommendation(payload) {
  const modelEl = document.getElementById("recommended-model");
  const expEl = document.getElementById("recommendation-explanation");
  const dateEl = document.getElementById("generated-at");
  if (modelEl) modelEl.textContent = payload.recommended_model;
  if (expEl) expEl.textContent = payload.explanation;
  if (dateEl) dateEl.textContent = formatDate(payload.generated_at);
}

function renderTable(payload) {
  const tbody = document.getElementById("eval-table-body");
  if (!tbody) return;

  const winner = payload.recommended_model;
  tbody.innerHTML = payload.models.map((m) => {
    const qualityClass = `quality-${m.quality.replace(/\s+/g, "-")}`;
    const winnerClass = m.model === winner ? "winner" : "";
    const winnerIcon = m.model === winner ? '<i class="fas fa-crown"></i>' : '<i class="fas fa-robot"></i>';

    return `
      <tr>
        <td><span class="model-pill ${winnerClass}">${winnerIcon} ${m.model}</span></td>
        <td>${m.mae.toFixed(3)}</td>
        <td>${m.rmse.toFixed(3)}</td>
        <td>${m.mape.toFixed(2)}%</td>
        <td><span class="quality-badge ${qualityClass}">${m.quality}</span></td>
        <td>${m.score.toFixed(3)}</td>
      </tr>
    `;
  }).join("");
}

function renderChart(payload) {
  const ctx = document.getElementById("eval-chart");
  if (!ctx) return;

  if (evalChart) evalChart.destroy();

  const labels = payload.models.map((m) => m.model);
  const mae = payload.models.map((m) => m.mae);
  const rmse = payload.models.map((m) => m.rmse);
  const mape = payload.models.map((m) => m.mape);

  evalChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "MAE (MW)",
          data: mae,
          backgroundColor: "rgba(59,130,246,0.75)",
        },
        {
          label: "RMSE (MW)",
          data: rmse,
          backgroundColor: "rgba(16,185,129,0.75)",
        },
        {
          label: "MAPE (%)",
          data: mape,
          backgroundColor: "rgba(245,158,11,0.75)",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "top",
        },
        tooltip: {
          callbacks: {
            label: (ctxObj) => `${ctxObj.dataset.label}: ${ctxObj.parsed.y.toFixed(3)}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          grid: {
            color: "rgba(0,0,0,0.08)",
          },
        },
      },
    },
  });
}

function renderInterpretation(payload) {
  const box = document.getElementById("interpretation-content");
  const decision = document.getElementById("decision-summary");
  if (!box || !decision) return;

  const winner = payload.models.find((m) => m.model === payload.recommended_model) || payload.models[0];
  const bestM = winner.mape.toFixed(2);

  decision.textContent = `${winner.model} is the best current choice for deployment because it has the lowest combined score and a ${winner.quality} MAPE (${bestM}%).`;

  box.innerHTML = `
    <p><strong>Executive summary:</strong> ${payload.explanation}</p>
    <p><strong>What this means operationally:</strong> choosing <strong>${winner.model}</strong> should produce the smallest forecasting errors in both absolute MW terms and relative percentage terms, reducing unnecessary alerts and underprediction risk.</p>
    <p><strong>How to maintain confidence:</strong> rerun evaluation after every retraining cycle, compare MAE/RMSE/MAPE trends, and switch recommendation only when another model consistently wins across multiple runs.</p>
    <p><strong>Metric guide:</strong></p>
    <p>- MAE: ${payload.metric_guide.mae}</p>
    <p>- RMSE: ${payload.metric_guide.rmse}</p>
    <p>- MAPE: ${payload.metric_guide.mape}</p>
  `;
}

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Unknown";
  return d.toLocaleString();
}

function showToast(msg, type = "info") {
  const toast = document.getElementById("toast");
  if (!toast) return;

  toast.textContent = msg;
  toast.className = "toast";
  if (type === "error") toast.style.background = "#dc2626";
  else if (type === "success") toast.style.background = "#16a34a";
  else toast.style.background = "#1a1d23";

  setTimeout(() => toast.classList.add("hidden"), 4000);
}
