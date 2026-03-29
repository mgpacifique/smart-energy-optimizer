# Model Evaluation Deployment Guide

## Overview

Your local machine has evaluated the Prophet, LSTM, and Ensemble models. The evaluation results are stored in `backend/data/eval_results.csv`. This guide shows you how to:

1. **Transfer** the evaluation file to your live servers
2. **Use** the new background evaluation system via tmux
3. **Monitor** evaluation progress without blocking the API

---

## Part 1: Quick Transfer (Recommended First Step)

If you want to immediately display results on your live dashboard, transfer the local eval file to both servers:

### Transfer to Web01:
```bash
scp /home/ovouz/smart-energy-optimizer/backend/data/eval_results.csv \
    user@WEB01_HOST:/path/to/smart-energy-optimizer/backend/data/
```

### Transfer to Web02:
```bash
scp /home/ovouz/smart-energy-optimizer/backend/data/eval_results.csv \
    user@WEB02_HOST:/path/to/smart-energy-optimizer/backend/data/
```

**Replace:**
- `user` = SSH user (e.g., `ubuntu`, `ec2-user`)
- `WEB01_HOST` = Your Web01 server IP/hostname
- `WEB02_HOST` = Your Web02 server IP/hostname

### Verify Transfer:
```bash
ssh user@WEB01_HOST "ls -lh /path/to/smart-energy-optimizer/backend/data/eval_results.csv"
```

**Result:** Your live dashboard will immediately show the evaluation summary without running evaluation.

---

## Part 2: New Background Evaluation System (Recommended for Production)

### What Changed?

**Before:**
- POST `/api/evaluation/run` would block for up to 10 minutes
- The request would timeout if the server was busy
- No feedback on progress

**Now:**
- POST `/api/evaluation/run` starts evaluation **in a tmux session** and returns immediately
- GET `/api/evaluation/status` polls for progress without blocking
- Frontend automatically polls and shows "Evaluating..." status
- Multiple requests are safe (won't start duplicate sessions)

### How It Works

```
User clicks "Run Evaluation Now"
    ↓
Frontend: POST /api/evaluation/run
    ↓
Backend: Creates/reuses tmux session "eval-momo"
    ↓
Backend: Starts: python evaluate.py (in tmux, logs to /tmp/eval-momo.log)
    ↓
Backend: Returns {"status": "started", ...}
    ↓
Frontend: Polls GET /api/evaluation/status every 3 seconds
    ↓
When status = "completed": Frontend calls loadEvaluation()
    ↓
User sees updated metrics and chart
```

### API Endpoints

#### 1. Start Background Evaluation
```
POST /api/evaluation/run
```

**Response (Immediate):**
```json
{
  "status": "started",
  "message": "Evaluation started in background (tmux session 'eval-momo')",
  "session": "eval-momo",
  "log_file": "/tmp/eval-momo.log"
}
```

Or if already running:
```json
{
  "status": "already_running",
  "message": "An evaluation is already running in tmux session 'eval-momo'",
  "session": "eval-momo",
  "log_file": "/tmp/eval-momo.log"
}
```

#### 2. Check Evaluation Status
```
GET /api/evaluation/status
```

**Response (While Running):**
```json
{
  "status": "running",
  "message": "Evaluation is in progress",
  "session_exists": true,
  "eval_file_exists": true,
  "log_tail": "...last 10 lines of evaluation log..."
}
```

**Response (Just Completed):**
```json
{
  "status": "completed",
  "message": "Evaluation just completed",
  "session_exists": false,
  "eval_file_exists": true,
  "eval_file_age_seconds": 45,
  "just_completed": true,
  "summary": {
    "generated_at": "...",
    "recommended_model": "LSTM",
    "models": [...]
  }
}
```

**Response (Idle):**
```json
{
  "status": "idle",
  "message": "No evaluation currently running",
  "session_exists": false,
  "eval_file_exists": true,
  "eval_file_age_seconds": 12345
}
```

#### 3. Get Latest Results
```
GET /api/evaluation/summary
```

Returns the latest evaluation metrics (same as before).

---

## Part 3: Deploy to Servers

### Prerequisites
- Ensure `tmux` is installed on Web01 and Web02
- Ensure `python evaluate.py` runs without errors (test locally first)

### Check if tmux is installed:
```bash
ssh user@WEB01_HOST "tmux --version"
```

If not installed:
```bash
# Ubuntu/Debian
ssh user@WEB01_HOST "sudo apt-get install -y tmux"

# Amazon Linux/RHEL
ssh user@WEB01_HOST "sudo yum install -y tmux"
```

### Deployment Steps

1. **Push code to GitHub:**
   ```bash
   cd /home/ovouz/smart-energy-optimizer
   git add backend/main.py frontend/simple/model-evaluation.html frontend/simple/model-evaluation.js
   git commit -m "feat: Add background evaluation with tmux and status polling"
   git push origin main
   ```

2. **Deploy to servers** (via your CI/CD or manual pull):
   ```bash
   ssh user@WEB01_HOST "cd /path/to/smart-energy-optimizer && git pull origin main"
   ssh user@WEB02_HOST "cd /path/to/smart-energy-optimizer && git pull origin main"
   ```

3. **Restart Uvicorn** on both servers:
   ```bash
   # SSH into each server and restart the backend
   # (your deployment method may vary)
   ssh user@WEB01_HOST "cd /path/to/smart-energy-optimizer/backend && pkill -f uvicorn && nohup uvicorn main:app --host 0.0.0.0 --port 8000 &"
   ```

4. **Test the new endpoints:**
   ```bash
   # Check if evaluation endpoint works
   curl -X POST http://WEB01_HOST:8000/api/evaluation/run
   
   # Check status
   curl http://WEB01_HOST:8000/api/evaluation/status
   
   # Check summary
   curl http://WEB01_HOST:8000/api/evaluation/summary
   ```

---

## Part 4: Recommendations

### For Immediate Production Use
1. ✅ Transfer `eval_results.csv` to both servers (Part 1)
2. ✅ Deploy the updated code (Part 3)
3. ✅ Test the new UI: Click "Run Evaluation Now" → Should show "Evaluating..."

### For Long-term Monitoring
1. Set up log rotation for `/tmp/eval-momo.log` (or move logs to persistent storage)
2. Add a cron job to run evaluations daily:
   ```bash
   # On each server, add to crontab:
   0 2 * * * curl -s -X POST http://localhost:8000/api/evaluation/run > /dev/null
   ```
3. Monitor `eval_results.csv` modification time to track when evaluations complete

### Troubleshooting

**Problem: "Evaluation is in progress" but nothing happening**
- Check the tmux session manually:
  ```bash
  tmux list-sessions
  tmux attach -t eval-momo
  ```
- Check the log file:
  ```bash
  tail -f /tmp/eval-momo.log
  ```

**Problem: Evaluation keeps failing**
- Run `python evaluate.py` manually to see the error:
  ```bash
  cd /path/to/backend && python evaluate.py
  ```
- Check that `backend/data/gatsibo_load.csv` exists (training data)

**Problem: Status API shows completed, but summary endpoint returns 404**
- The evaluation script may have crashed silently—check `/tmp/eval-momo.log`
- Try running `python evaluate.py` manually on the server

---

## Next Steps

1. **Now:** Transfer the eval file to servers (Part 1) — takes 2 minutes
2. **This week:** Deploy the new code (Part 3) — takes 5 minutes  
3. **This month:** Set up daily evaluation cron job for continuous monitoring

---

## File Locations

| Component | Path |
|-----------|------|
| Evaluation Script | `backend/evaluate.py` |
| Results CSV | `backend/data/eval_results.csv` |
| Training Data | `backend/data/gatsibo_load.csv` |
| Backend API | `backend/main.py` (lines 228–380) |
| Frontend Page | `frontend/simple/model-evaluation.html` |
| Frontend JS | `frontend/simple/model-evaluation.js` |
| Evaluation Log | `/tmp/eval-momo.log` (on servers) |
| Tmux Session | `eval-momo` (on servers) |

---

## Local Testing (Before Deploying)

Want to test the new system locally before deploying?

```bash
cd /home/ovouz/smart-energy-optimizer/backend

# Terminal 1: Start Uvicorn
source /home/ovouz/.venv/bin/activate
uvicorn main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2: Test the endpoints
# Start evaluation in background
curl -X POST http://127.0.0.1:8000/api/evaluation/run

# Check status (poll a few times)
curl http://127.0.0.1:8000/api/evaluation/status

# Get results when complete
curl http://127.0.0.1:8000/api/evaluation/summary

# Open browser to http://localhost:8000/simple/model-evaluation.html
# Click "Run Evaluation Now" and watch the status update
```

---

## Summary: Your Evaluation Pipeline

**Current State:**
- ✅ Local evaluation: `Prophet=35.4%, LSTM=24%, Ensemble=27.6% MAPE`
- ❌ Results not on servers yet

**After Part 1 (Transfer File):**
- ✅ Results visible on live dashboard immediately
- ❌ Can't run new evaluations from the web

**After Part 2+3 (Deploy Code):**
- ✅ Results visible on dashboard
- ✅ Can click "Run Evaluation Now" button
- ✅ Evaluation runs in background without blocking API
- ✅ Frontend polls and shows progress
- ✅ Results update when evaluation completes

---

**Questions? Check `/tmp/eval-momo.log` on the server or run `tmux attach -t eval-momo` to see live output.**
