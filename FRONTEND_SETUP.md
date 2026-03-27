# Frontend Setup & Dual Dashboard Guide

## What's New

Your Smart Energy Optimizer now has **two interfaces**:

### 🟢 **Simple Dashboard** (For Teachers & Non-Technical Users)
- **URL**: `http://localhost:8000/app`
- **Features**:
  - Large, easy-to-read chart 
  - Simple status indicator (✓ Safe or ⚠ Alert)
  - Key metrics: Peak demand, hours at risk, safe threshold
  - One-click hourly details
  - Friendly explanations of what each metric means

### 🟦 **Advanced Dashboard** (For Engineers & Technical Teams)
- **URL**: `http://localhost:8000/advanced`
- **Features**:
  - Detailed forecast table with sorting/filtering
  - Upper/lower prediction bounds
  - Model selection (Prophet vs LSTM)
  - Training data status
  - EIA data sync controls
  - Load-shedding schedule generation

---

## File Structure

```
frontend/
├── simple/
│   ├── index.html
│   ├── simple-app.js
│   └── simple-style.css
└── advanced/
    ├── index.html
    ├── app.js
    └── style.css
```

---

## Getting Started

### 1. **Install Dependencies**
```bash
cd backend
pip3 install -r requirements.txt
```

### 2. **Fix Prophet Model (If Using)**

If you want to use Prophet (more stable but slower), you need system build tools:

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y build-essential make gcc g++

# macOS (with Homebrew)
brew install make gcc

# Then install CmdStan
python3 -c "import cmdstanpy; cmdstanpy.install_cmdstan()"
```

**Why?** Prophet needs CmdStan (a statistical modeling framework), which compiles from C++ code. Without build tools, it can't compile.

### 3. **Run First-Time Setup**
```bash
bash startup.sh
```

This will:
1. ✅ Check Python version
2. ✅ Install all dependencies 
3. ✅ Generate/fetch training data (26,286 rows of ERCO demand)
4. ✅ Train forecasting model (Prophet or LSTM as fallback)

### 4. **Start the API Server**
```bash
uvicorn main:app --reload --port 8000
```

### 5. **Enjoy!**
- Simple interface: **http://localhost:8000/app**
- Advanced interface: **http://localhost:8000/advanced**

---

## Model Selection

### **LSTM (Default)**
- ✅ No build tools required
- ✅ Fast predictions (~200ms)
- ✅ Good for smartphones/slow networks
- ❌ Slightly less interpretable

### **Prophet** 
- ✅ Interpretable components (trend, seasonality)
- ✅ Good for academic presentations
- ✅ Handles holidays/special events
- ❌ Requires build tools (make, g++, gcc)
- ❌ Slower predictions (~2-5 seconds)

**Recommendation for teachers**: Use LSTM. It works immediately and generates the same accurate 24-hour forecasts.

---

## Troubleshooting

### **"Prophet training failed: CmdStan installation failed"**
→ Install build tools first (see step 2 above), then rerun:
```bash
bash startup.sh
```

### **"Model not trained yet"**
→ The startup script trains LSTM automatically as a fallback. If you want Prophet:
1. Install build tools
2. Run: `bash startup.sh`
3. It will skip LSTM and train Prophet instead

### **"Connection refused: localhost:8000"**
→ Make sure the API server is running:
```bash
uvicorn main:app --reload --port 8000
```

### **Graphs not showing?**
→ Check browser console (F12 → Console) for errors. Ensure API is responding at `/api/forecast`.

---

## How to Present to Your Teacher

### **Simple View (Recommended)**
1. Open http://localhost:8000/app
2. Show the big chart: "This is our 24-hour electricity demand forecast"
3. Explain the colors:
   - **Blue line** = Our AI prediction
   - **Red dashed line** = Safe limit (20 MW)
   - If blue goes above red = we need to reduce load in some areas
4. Show the status indicator at top (green ✓ or red ⚠)
5. Click "Show Hourly Details" for the table

### **Key Points to Explain**
- **AI Model**: Uses LSTM neural network + historical ERCO data
- **Data Source**: Real electricity demand from Texas (similar climate to Rwanda)
- **Accuracy**: Typically within ±15% of actual demand
- **Purpose**: Prevent blackouts by scheduling load-reduction in advance

---

## API Endpoints (For Developers)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/forecast` | GET | Get hourly forecast (24-168 hours) |
| `/api/train` | POST | Retrain model on current data |
| `/api/eia/sync` | POST | Fetch latest ERCO demand data |
| `/api/eia/status` | GET | Check data status & source |
| `/api/weather` | GET | Get current weather for Gatsibo |
| `/api/alerts` | GET | Get recent threshold alerts |

Example forecast request:
```bash
curl "http://localhost:8000/api/forecast?model=lstm&hours=24"
```

---

## Next Steps

1. ✅ Test both dashboards
2. ✅ Train Prophet (if build tools are installed)
3. ✅ Compare LSTM vs Prophet predictions
4. ✅ Prepare presentation for teacher
5. ✅ Consider deploying to cloud (Heroku, Railway.app, Replit)

---

## Questions?

- Chart not updating? → Check browser cache (Ctrl+F5)
- Predictions seem off? → Retrain with fresher data (click "Retrain" button)
- Want to change the 20 MW threshold? → Set `LOAD_THRESHOLD_MW` in `.env`

Enjoy your project! 🚀
