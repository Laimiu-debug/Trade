#!/bin/bash

echo "============================================"
echo "Final Trade - 快速启动脚本"
echo "============================================"
echo ""

echo "Building journal frontend..."
(cd journal-frontend && npm ci && npm run build) || exit 1

echo "[1/3] 启动后端服务..."
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo "后端启动中... PID: $BACKEND_PID"
sleep 5

echo ""
echo "[2/3] 检查后端服务..."
if curl -s http://127.0.0.1:8000/api/config > /dev/null 2>&1; then
    echo "✓ 后端服务启动成功！"
    echo "  API地址: http://127.0.0.1:8000"
else
    echo "✗ 后端服务启动失败，请检查错误信息"
    exit 1
fi

echo ""
echo "[3/3] 启动前端服务..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!
echo "前端启动中... PID: $FRONTEND_PID"
sleep 5

echo ""
echo "============================================"
echo "启动完成！"
echo "============================================"
echo ""
echo "前端地址: http://localhost:4173"
echo "后端API:  http://127.0.0.1:8000"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 等待用户中断
trap "echo ''; echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT

# 保持脚本运行
wait
