#!/bin/bash
# startup.sh
# First-run bootstrap for the Smart Energy Optimizer backend.
# Run this ONCE before starting the API to generate data and train models.
#
# Usage:
#   chmod +x startup.sh
#   ./startup.sh

set -e

echo ""
echo "======================================================"
echo "  Texas ERCOT Smart Energy Optimizer — First-run Setup"
echo "======================================================"
echo ""

# ── 1. Check Python version ───────────────────────────────────────────────────
echo "[1/4] Checking Python version..."
python3 --version || { echo "ERROR: Python 3 not found."; exit 1; }

# ── 2. Install dependencies ───────────────────────────────────────────────────
echo ""
echo "[2/4] Installing Python dependencies..."
pip3 install -r requirements.txt --quiet

# ── 3. Fetch EIA data (or generate fallback) ──────────────────────────────────
echo ""
echo "[3/4] Fetching ERCO demand data from EIA API..."
echo "      (Falls back to synthetic data if EIA_API_KEY is not set)"
python3 -c "
import logging, os
logging.basicConfig(level=logging.INFO)
from eia_loader import generate
df = generate()
print(f'Dataset ready: {len(df):,} rows | load range: {df[\"load_mw\"].min():.1f}–{df[\"load_mw\"].max():.1f} MW')
"

# ── 4. Train Prophet model ────────────────────────────────────────────────────
echo ""
echo "[4/4] Training forecasting model..."
python3 -c "
import logging
logging.basicConfig(level=logging.INFO)
from forecaster import ProphetForecaster
try:
	fc = ProphetForecaster()
	fc.train()
	print('Prophet model trained and saved.')
except Exception as exc:
	print(f'Prophet training failed: {exc}')
	print('Falling back to LSTM training...')
	from lstm_model import LSTMForecaster
	lf = LSTMForecaster()
	lf.train()
	print('LSTM model trained and saved.')
"

echo ""
echo "======================================================"
echo "  Setup complete! Start the API with:"
echo ""
echo "    uvicorn main:app --reload --port 8000"
echo ""
echo "  Or with Docker Compose from the project root:"
echo ""
echo "    docker compose up --build"
echo ""
echo "  Dashboard: http://localhost:8000/app"
echo "  API docs:  http://localhost:8000/docs"
echo "======================================================"
echo ""
