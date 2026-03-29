#!/bin/bash

# deploy-eval-results.sh
# Quick script to transfer evaluation results to live servers

set -e

# Configuration - UPDATE THESE!
WEB01_USER="ubuntu"
WEB01_HOST="3.82.209.129"
WEB02_USER="ubuntu"
WEB02_HOST="184.72.182.76"
BACKEND_PATH="/path/to/smart-energy-optimizer/backend"

LOCAL_EVAL_FILE="/home/ovouz/smart-energy-optimizer/backend/data/eval_results.csv"
REMOTE_EVAL_DIR="data"

echo "================================================"
echo "Smart Energy Optimizer - Evaluation Deployment"
echo "================================================"
echo ""

# Verify local file exists
if [ ! -f "$LOCAL_EVAL_FILE" ]; then
    echo "❌ ERROR: Local evaluation file not found: $LOCAL_EVAL_FILE"
    exit 1
fi

echo "✓ Found local evaluation file"
echo "  File: $LOCAL_EVAL_FILE"
echo "  Size: $(du -h "$LOCAL_EVAL_FILE" | cut -f1)"
echo ""

# Display content
echo "Content Preview:"
head -2 "$LOCAL_EVAL_FILE"
echo ""

# Confirm before proceeding
read -p "Deploy to Web01 ($WEB01_HOST) and Web02 ($WEB02_HOST)? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo "================================================"
echo "Deploying to Web Servers..."
echo "================================================"
echo ""

# Deploy to Web01
echo "→ Deploying to Web01 ($WEB01_HOST)..."
scp "$LOCAL_EVAL_FILE" \
    "${WEB01_USER}@${WEB01_HOST}:${BACKEND_PATH}/${REMOTE_EVAL_DIR}/" 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Web01 deployment successful"
else
    echo "❌ Web01 deployment failed"
fi

echo ""

# Deploy to Web02
echo "→ Deploying to Web02 ($WEB02_HOST)..."
scp "$LOCAL_EVAL_FILE" \
    "${WEB02_USER}@${WEB02_HOST}:${BACKEND_PATH}/${REMOTE_EVAL_DIR}/" 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Web02 deployment successful"
else
    echo "❌ Web02 deployment failed"
fi

echo ""
echo "================================================"
echo "Verification"
echo "================================================"
echo ""

# Verify files exist on servers
echo "✓ Verifying Web01..."
ssh "${WEB01_USER}@${WEB01_HOST}" "ls -lh ${BACKEND_PATH}/${REMOTE_EVAL_DIR}/eval_results.csv" 2>&1 || echo "⚠ Could not verify Web01"

echo ""
echo "✓ Verifying Web02..."
ssh "${WEB02_USER}@${WEB02_HOST}" "ls -lh ${BACKEND_PATH}/${REMOTE_EVAL_DIR}/eval_results.csv" 2>&1 || echo "⚠ Could not verify Web02"

echo ""
echo "================================================"
echo "Deployment Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Visit https://lb-01.booklogger.tech/simple/model-evaluation.html"
echo "2. Refresh the page to see the evaluation results"
echo "3. Click 'Run Evaluation Now' to test the new background system"
echo ""
