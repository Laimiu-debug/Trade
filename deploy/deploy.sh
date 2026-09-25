#!/bin/bash
#=============================================
# Final Trade - Ubuntu 服务器一键部署脚本
#
# 数据方案: Windows TDX 盘后同步
#   Windows 本地运行通达信 → 收盘后运行 sync-tdx-data.ps1
#   → 将 vipdoc 数据上传到服务器的 /opt/final-trade/tdx-data
#   → 后端以 tdx_only 模式读取
#=============================================

set -e

# ---------- 配置区 ----------
PROJECT_NAME="final-trade"
INSTALL_DIR="/opt/final-trade"          # 项目安装目录
TDX_DATA_DIR="${INSTALL_DIR}/tdx-data"  # TDX 数据存放目录
NGINX_CONF="/etc/nginx/sites-available/final-trade"
PYTHON_VERSION="python3.12"
NODE_VERSION="20"
BACKEND_PORT=8000
FRONTEND_PORT=80
DOMAIN=""                               # 填入你的域名，如 trade.example.com（留空则用 IP）

echo "============================================"
echo " Final Trade - Ubuntu 部署脚本"
echo "============================================"
echo ""

# ===========================================
# 1. 安装系统依赖
# ===========================================
echo "[1/5] 安装系统依赖..."

sudo apt update
sudo apt install -y \
    curl wget git nginx \
    software-properties-common \
    ca-certificates gnupg \
    build-essential

# 安装 Node.js (通过 NodeSource)
if ! command -v node &>/dev/null; then
    echo "  安装 Node.js ${NODE_VERSION}..."
    curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | sudo -E bash -
    sudo apt install -y nodejs
fi
echo "  Node.js: $(node -v)"
echo "  npm:     $(npm -v)"

# 安装 Python
if ! command -v ${PYTHON_VERSION} &>/dev/null; then
    echo "  安装 Python 3.12..."
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt install -y ${PYTHON_VERSION} ${PYTHON_VERSION}-venv ${PYTHON_VERSION}-dev
fi
echo "  Python:  $(${PYTHON_VERSION} --version)"

sudo apt install -y python3-pip

echo "  ✓ 系统依赖安装完成"
echo ""

# ===========================================
# 2. 复制项目文件
# ===========================================
echo "[2/5] 部署项目文件..."

if [ ! -d "${INSTALL_DIR}" ]; then
    sudo mkdir -p "${INSTALL_DIR}"
fi

echo "  项目目录: ${INSTALL_DIR}"
echo ""

# 检查文件是否存在
if [ ! -f "${INSTALL_DIR}/backend/app/main.py" ]; then
    echo "  ✗ 错误: 未找到项目文件！"
    echo "    请先将项目上传到 ${INSTALL_DIR}"
    echo ""
    echo "    推荐上传方式（在本地执行）:"
    echo "    scp -r ./backend ./frontend deploy/ ${USER}@<服务器IP>:/tmp/final-trade/"
    echo "    sudo mv /tmp/final-trade /opt/final-trade"
    echo ""
    exit 1
fi

echo "  ✓ 项目文件就绪"
echo ""

# ===========================================
# 3. 配置后端 (FastAPI)
# ===========================================
echo "[3/5] 配置后端..."

cd "${INSTALL_DIR}/backend"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "  创建 Python 虚拟环境..."
    ${PYTHON_VERSION} -m venv venv
fi

# 安装依赖
echo "  安装 Python 依赖..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# 创建 TDX 数据目录结构
echo "  创建 TDX 数据目录..."
sudo mkdir -p "${TDX_DATA_DIR}"/{sh/lday,sh/minline,sz/lday,sz/minline,bj/lday,bj/minline,T0002/hq_cache}
sudo chown -R www-data:www-data "${TDX_DATA_DIR}"

echo "  ✓ 后端配置完成"
echo "  TDX 数据目录: ${TDX_DATA_DIR}"
echo "  ⚠ 部署后需从 Windows 上传 TDX 数据，后端才能正常工作"
echo ""

# ===========================================
# 4. 构建前端 (Vite + React)
# ===========================================
echo "[4/5] 构建前端..."

cd "${INSTALL_DIR}/frontend"

# 安装依赖
if [ ! -d "node_modules" ]; then
    echo "  安装前端依赖..."
    npm install
fi

# 构建生产版本
echo "  构建生产版本..."
npm run build

echo "  ✓ 前端构建完成 (dist/)"
echo ""

# ===========================================
# 5. 配置 Nginx + systemd
# ===========================================
echo "[5/5] 配置 Nginx 和系统服务..."

# --- systemd 服务文件 ---
sudo tee /etc/systemd/system/final-trade-backend.service > /dev/null <<SERVICE
[Unit]
Description=Final Trade Backend (FastAPI)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/final-trade/backend
ExecStart=/opt/final-trade/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable final-trade-backend
sudo systemctl restart final-trade-backend
echo "  ✓ 后端服务已启动"

# --- Nginx 配置 ---
sudo tee "${NGINX_CONF}" > /dev/null <<NGINX
server {
    listen 80;
    server_name ${DOMAIN:-_};

    # 前端静态文件
    root /opt/final-trade/frontend/dist;
    index index.html;

    # API 反向代理 -> FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # SPA 路由 fallback
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # 静态资源缓存
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml;
    gzip_min_length 1024;
}
NGINX

# 启用站点
sudo ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# 测试并重启 Nginx
sudo nginx -t && sudo systemctl restart nginx

echo "  ✓ Nginx 配置完成"
echo ""

# ===========================================
# 完成
# ===========================================
SERVER_IP=$(hostname -I | awk '{print $1}')
echo "============================================"
echo " 部署完成！"
echo "============================================"
echo ""
echo "  前端访问:  http://${DOMAIN:-$SERVER_IP}"
echo "  后端 API:  http://127.0.0.1:${BACKEND_PORT}"
echo ""
echo "  ⚠ 下一步：从 Windows 上传 TDX 数据"
echo "  ─────────────────────────────────────"
echo "  1. Windows 本地运行通达信，等待数据更新完"
echo "  2. 修改 deploy/scripts/sync-tdx-data.ps1 中的配置"
echo "     - TDX_PATH: 你的通达信 vipdoc 路径"
echo "     - SERVER_HOST: ${SERVER_IP}"
echo "  3. PowerShell 执行:"
echo "     .\deploy\scripts\sync-tdx-data.ps1"
echo "  4. 在应用设置中将数据源配置为:"
echo "     - tdx_data_path: ${TDX_DATA_DIR}"
echo "     - market_data_source: tdx_only"
echo ""
echo "  常用命令:"
echo "    查看后端日志:  sudo journalctl -u final-trade-backend -f"
echo "    重启后端:      sudo systemctl restart final-trade-backend"
echo "    重启 Nginx:    sudo systemctl restart nginx"
echo ""

# 可选：HTTPS 提示
if [ -n "${DOMAIN}" ]; then
    echo "  配置 HTTPS（可选）:"
    echo "    sudo apt install certbot python3-certbot-nginx -y"
    echo "    sudo certbot --nginx -d ${DOMAIN}"
    echo ""
fi
