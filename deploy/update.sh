#!/bin/bash
#=============================================
# Final Trade - 更新部署脚本
# 在服务器上执行，用于更新代码后重新部署
#=============================================

set -euo pipefail

INSTALL_DIR="/opt/final-trade"

echo "============================================"
echo " Final Trade - 更新部署"
echo "============================================"
echo ""

# --- 后端更新 ---
echo "[1/3] 更新后端..."
cd "${INSTALL_DIR}/backend"

source venv/bin/activate
pip install -r requirements.txt
deactivate

echo "  ✓ 后端依赖已更新，待前端构建成功后重启"
echo ""

# --- 前端更新 ---
echo "[2/3] 更新前端..."
cd "${INSTALL_DIR}/frontend"

npm ci
npm run build

cd "${INSTALL_DIR}/journal-frontend"
npm ci
npm run build

echo "  ✓ 前端已重新构建"
echo ""

# --- 重载 Nginx ---
echo "[3/3] 重载 Nginx..."
sudo nginx -t
sudo systemctl restart final-trade-backend
sudo systemctl reload nginx
echo "  ✓ Nginx 已重载"
echo ""

echo "============================================"
echo " 更新完成！"
echo "============================================"
