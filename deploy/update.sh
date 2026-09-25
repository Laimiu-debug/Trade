#!/bin/bash
#=============================================
# Final Trade - 更新部署脚本
# 在服务器上执行，用于更新代码后重新部署
#=============================================

set -e

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

sudo systemctl restart final-trade-backend
echo "  ✓ 后端已更新并重启"
echo ""

# --- 前端更新 ---
echo "[2/3] 更新前端..."
cd "${INSTALL_DIR}/frontend"

npm install
npm run build

echo "  ✓ 前端已重新构建"
echo ""

# --- 重载 Nginx ---
echo "[3/3] 重载 Nginx..."
sudo systemctl reload nginx
echo "  ✓ Nginx 已重载"
echo ""

echo "============================================"
echo " 更新完成！"
echo "============================================"
