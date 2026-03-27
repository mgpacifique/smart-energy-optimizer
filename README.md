# Gatsibo Smart Energy Consumption Optimizer

A predictive infrastructure application that forecasts peak electricity load for **Gatsibo District, Eastern Province, Rwanda**, and automatically triggers load-shedding recommendations when predicted demand exceeds the configured threshold.

---

## Overview

The application fetches real-time weather data from **Open-Meteo**, combines it with historical load patterns, and runs time-series forecasting using **Prophet** (primary) and **LSTM** (secondary). When the predicted load exceeds 20 MW, a webhook fires to the smart-grid controller dashboard and alerts are sent via **Africa's Talking SMS** and **Resend email**.

### Key features
- 24–168 hour load forecasts with confidence intervals
- Two switchable models: Prophet and LSTM
- Automatic threshold alerts with zone-based load-shedding schedules
- Redis caching for weather API responses
- Sortable, filterable, searchable forecast dashboard
- Deployed on two web servers behind an Nginx load balancer

---

## APIs & Credits

| Service | Purpose | Documentation |
|---|---|---|
| [Open-Meteo](https://open-meteo.com) | Weather data (temperature, humidity, solar radiation) — free, no API key | https://open-meteo.com/en/docs |
| [Africa's Talking](https://africastalking.com) | SMS alerts to grid operators | https://developers.africastalking.com |
| [Resend](https://resend.com) | Email alert delivery | https://resend.com/docs |

---

## Local Setup

### Prerequisites
- Python 3.11+
- Docker & Docker Compose (optional but recommended)
- Redis (or use Docker Compose which includes it)

### 1. Clone the repository
```bash
git clone https://github.com/YOUR_USERNAME/smart-energy-optimizer.git
cd smart-energy-optimizer
```

### 2. Configure environment variables
```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your API keys
```

### 3a. Run with Docker Compose (recommended)
```bash
docker compose up --build
```
The API will be available at `http://localhost:8000`.

### 3b. Run without Docker
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Generate synthetic data and train the Prophet model
python data_gen.py
python forecaster.py   # trains and saves model

# Start the API
uvicorn main:app --reload --port 8000
```

### 4. Open the dashboard
Open `frontend/index.html` in your browser, or navigate to `http://localhost:8000/app`.

### 5. Train the LSTM model (optional, takes ~2 minutes)
```bash
# Via the dashboard "Retrain" button (select LSTM first), or:
curl -X POST http://localhost:8000/api/train \
  -H "Content-Type: application/json" \
  -d '{"model": "lstm"}'
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/api/forecast?model=prophet&hours=24` | Load forecast |
| POST | `/api/train` | Retrain a model |
| POST | `/webhook/controller` | Mock grid controller receiver |
| GET | `/api/alerts` | Recent alert log |
| GET | `/api/weather` | Current Gatsibo weather |

Interactive API docs: `http://localhost:8000/docs`

---

## Deployment

### Server setup (Web01 & Web02)

SSH into each server and run:

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Clone the repo
git clone https://github.com/YOUR_USERNAME/smart-energy-optimizer.git
cd smart-energy-optimizer

# Add your .env file
cp backend/.env.example backend/.env
nano backend/.env   # fill in your keys

# Train the models before starting
docker compose run api python data_gen.py
docker compose run api python forecaster.py

# Start services
docker compose up -d
```

Repeat on both Web01 and Web02.

### Load balancer configuration (Lb01)

```bash
# Install Nginx
sudo apt update && sudo apt install -y nginx

# Copy the config (replace IPs first)
sudo nano /etc/nginx/sites-available/energy-optimizer
# Paste contents of nginx/lb01.conf
# Replace WEB01_IP, WEB02_IP, LB01_IP with actual addresses

sudo ln -s /etc/nginx/sites-available/energy-optimizer /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Testing the load balancer

Make several requests and check the `X-Served-By` header alternates between Web01 and Web02:

```bash
for i in {1..6}; do
  curl -s -I http://LB01_IP/api/ | grep X-Served-By
done
```

---

## CI/CD (GitHub Actions)

Add these secrets to your GitHub repository (Settings → Secrets → Actions):

| Secret | Value |
|---|---|
| `WEB01_HOST` | Web01 IP address |
| `WEB02_HOST` | Web02 IP address |
| `LB01_HOST` | Lb01 IP address |
| `SSH_USER` | Your SSH username (e.g. `ubuntu`) |
| `SSH_PRIVATE_KEY` | Contents of your private SSH key |

Every push to `main` will run tests, then deploy to both servers automatically.

---

## Project Structure

```
smart-energy-optimizer/
├── backend/
│   ├── main.py          FastAPI app + routes
│   ├── forecaster.py    Prophet model
│   ├── lstm_model.py    LSTM model (Keras)
│   ├── weather.py       Open-Meteo fetcher + Redis cache
│   ├── webhook.py       Threshold checker + dispatcher
│   ├── alerts.py        Africa's Talking + Resend
│   ├── scheduler.py     Hourly APScheduler job
│   ├── data_gen.py      Synthetic Gatsibo load data
│   └── requirements.txt
├── frontend/
│   ├── index.html       Dashboard
│   ├── style.css
│   └── app.js
├── nginx/
│   └── lb01.conf        Load balancer config
├── .github/workflows/
│   └── deploy.yml       CI/CD pipeline
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Challenges & Solutions

**Synthetic data realism** — Without access to real RURA/REG data, I modelled Gatsibo's load profile using diurnal sinusoidal patterns, weekday/weekend differentials, and Rwanda's dry/rainy season cycles. The model performs well on this synthetic data and can be replaced with real data by swapping the CSV.

**Prophet + LSTM coexistence** — Both models use the same data pipeline but have different inference patterns. Prophet is fast and interpretable; LSTM uses autoregressive inference for multi-step forecasting, making it slower but more flexible.

**Redis fallback** — The app degrades gracefully if Redis is unavailable, falling back to direct API calls. This was important for deployment environments where Redis may not be configured.

---

## License

MIT — see LICENSE file.
