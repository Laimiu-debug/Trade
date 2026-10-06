#!/bin/bash
#=============================================
# Final Trade - Ubuntu 服务器一键部署脚本
#
# 数据方案: Windows TDX 盘后同步
#   Windows 本地运行通达信 → 收盘后运行 sync-tdx-data.ps1
#   → 将 vipdoc 数据上传到服务器的 /opt/final-trade/tdx-data
#   → 后端以 tdx_only 模式读取
#=============================================

set -euo pipefail

# ---------- 配置区 ----------
PROJECT_NAME="final-trade"
INSTALL_DIR="/opt/final-trade"          # 项目安装目录
TDX_DATA_DIR="${INSTALL_DIR}/tdx-data"  # TDX 数据存放目录
NGINX_CONF="/etc/nginx/sites-available/final-trade"
PYTHON_VERSION="python3.12"
NODE_VERSION="22"
BACKEND_PORT=8000
PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-}"       # 必填，例如 https://trade.example.com
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"       # 必填，用于证书续期通知
TRADE_AUTH_USER="${TRADE_AUTH_USER:-trade}"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DOMAIN=$(python3 "${SCRIPT_DIR}/legacy_nginx.py" "${PUBLIC_ORIGIN}" --domain-only)
if [[ ! "${CERTBOT_EMAIL}" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || \
   [[ ! "${TRADE_AUTH_USER}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    echo "请设置 CERTBOT_EMAIL 和有效的 TRADE_AUTH_USER。"
    exit 1
fi

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
    curl wget git nginx certbot apache2-utils \
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
node -e 'const [a,b]=process.versions.node.split(".").map(Number);if(!((a===20&&b>=19)||(a===22&&b>=12)||a>22)){console.error("Node.js ^20.19.0 or >=22.12.0 is required");process.exit(1)}'

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
    echo "    scp -r ./backend ./frontend ./journal-frontend deploy/ ${USER}@<服务器IP>:/tmp/final-trade/"
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

npm ci

# 构建生产版本
echo "  构建生产版本..."
npm run build

cd "${INSTALL_DIR}/journal-frontend"
npm ci
npm run build

echo "  ✓ 前端构建完成 (dist/)"
echo ""

# ===========================================
# 5. 配置 Nginx + systemd
# ===========================================
echo "[5/5] 配置 Nginx 和系统服务..."

# 所有页面和接口均在 HTTPS 下认证；密码由 htpasswd 交互读取，不进入参数或日志。
if [ ! -s /etc/nginx/final-trade.htpasswd ]; then
    sudo htpasswd -cB /etc/nginx/final-trade.htpasswd "${TRADE_AUTH_USER}"
fi
sudo chown root:www-data /etc/nginx/final-trade.htpasswd
sudo chmod 640 /etc/nginx/final-trade.htpasswd
sudo mkdir -p "${INSTALL_DIR}/data" /var/www/.tdx-trend /var/www/letsencrypt
sudo chown -R www-data:www-data "${INSTALL_DIR}/data" /var/www/.tdx-trend

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
Environment=TRADING_MS_ALLOWED_ORIGINS=${PUBLIC_ORIGIN}
Environment=TRADING_MS_DATA_DIR=${INSTALL_DIR}/data

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable final-trade-backend
sudo systemctl restart final-trade-backend
echo "  ✓ 后端服务已启动"

# 申请证书前只暴露 ACME 验证路径与 HTTPS 跳转，不通过 HTTP 暴露应用。
python3 "${SCRIPT_DIR}/legacy_nginx.py" "${PUBLIC_ORIGIN}" --bootstrap | sudo tee "${NGINX_CONF}" > /dev/null
sudo ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo certbot certonly --webroot -w /var/www/letsencrypt -d "${DOMAIN}" \
    --email "${CERTBOT_EMAIL}" --agree-tos --non-interactive \
    --deploy-hook "systemctl reload nginx"

# 有效证书就绪后启用带认证的 HTTPS 页面和 API。
python3 "${SCRIPT_DIR}/legacy_nginx.py" "${PUBLIC_ORIGIN}" | sudo tee "${NGINX_CONF}" > /dev/null
sudo nginx -t
sudo systemctl reload nginx

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
echo "  前端访问:  ${PUBLIC_ORIGIN}（需要登录）"
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

echo "  来源与认证已配置；证书由 certbot 自动续期。"
