# Final Trade - Ubuntu 部署指南

## 数据方案：Windows TDX 同步

Ubuntu 服务器上不运行 TDX，而是：

```
Windows 通达信 (盘后自动更新数据)
    ↓ 收盘后运行 sync-tdx-data.ps1
scp 上传 .day / .lc1 / .tnf 文件
    ↓
Ubuntu 服务器 /opt/final-trade/tdx-data/
    ↓ 后端 tdx_only 模式读取
FastAPI 分析展示
```

## 一、服务器部署

### 1. 上传项目到服务器

```bash
# 本地执行
scp -r ./backend ./frontend deploy/ ${USER}@<服务器IP>:/tmp/final-trade/
```

### 2. SSH 登录服务器，执行部署

```bash
sudo mv /tmp/final-trade /opt/final-trade
chmod +x /opt/final-trade/deploy/deploy.sh
sudo bash /opt/final-trade/deploy/deploy.sh
```

### 3. 配置 SSH 免密（让 Windows 能自动上传）

```bash
# 在服务器上执行
sudo -u www-data mkdir -p /var/www/.ssh
sudo -u www-data bash -c 'cat >> /var/www/.ssh/authorized_keys << EOF
粘贴你 Windows 的公钥内容 (C:\Users\你的用户名\.ssh\id_rsa.pub)
EOF'
sudo chmod 700 /var/www/.ssh
sudo chmod 600 /var/www/.ssh/authorized_keys
```

## 二、Windows 端数据同步

### 1. 修改配置

编辑 `deploy/scripts/sync-tdx-data.ps1`：

```powershell
# 修改这三个值
$TDX_PATH = "D:\new_tdx\vipdoc"       # 你的通达信 vipdoc 路径
$SERVER_USER = "root"                   # 服务器 SSH 用户
$SERVER_HOST = "192.168.1.100"         # 服务器 IP
```

### 2. 手动同步

```powershell
# PowerShell 执行
.\deploy\scripts\sync-tdx-data.ps1
```

### 3. 设置 Windows 定时任务（每天收盘后自动同步）

```powershell
# PowerShell 管理员执行
# 每天 16:00 自动同步（A 股 15:00 收盘，留 1 小时缓冲）
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$PWD\deploy\scripts\sync-tdx-data.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At "16:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "FinalTrade-TDX-Sync" -Action $action -Trigger $trigger -Settings $settings -Description "同步通达信数据到服务器"

# 查看任务
Get-ScheduledTask -TaskName "FinalTrade-TDX-Sync"

# 手动触发测试
Start-ScheduledTask -TaskName "FinalTrade-TDX-Sync"
```

## 三、应用配置

部署完成后，在应用 Web 界面的设置页面配置：

| 配置项 | 值 |
|--------|-----|
| TDX 数据路径 | `/opt/final-trade/tdx-data` |
| 数据源 | `tdx_only` |

或通过 API 配置：

```bash
curl -X POST http://localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "tdx_data_path": "/opt/final-trade/tdx-data",
    "market_data_source": "tdx_only"
  }'
```

## 日常维护

```bash
# 查看后端日志
sudo journalctl -u final-trade-backend -f

# 重启后端
sudo systemctl restart final-trade-backend

# 重启 Nginx
sudo systemctl restart nginx

# 检查 TDX 数据文件
ls /opt/final-trade/tdx-data/sh/lday/ | head -10
ls /opt/final-trade/tdx-data/sz/lday/ | head -10
```

## 配置 HTTPS（有域名时）

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

## 防火墙

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```
