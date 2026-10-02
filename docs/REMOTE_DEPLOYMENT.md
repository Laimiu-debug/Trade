# 新应用远程部署契约

新后端保持 `127.0.0.1:8011`。远程入口采用 **Caddy HTTPS + 全站 Basic 登录**，成功认证后由代理覆盖独立的内部令牌。应用同时检查唯一 HTTPS origin、Host、实际连接方、代理令牌、会话与 CSRF。默认不设置远程变量时仍为本机模式。

这套配置已通过应用边界测试及真实 Caddy 2.11.4 本机 HTTPS 集成测试；尚未安装到用户服务器，也未验证实际域名、公网证书或 Linux 服务。原 `deploy` 中旧应用脚本不能直接用于新应用。

## 目录与服务

- 新应用源码和构建静态目录：`/opt/trade`；Python 环境：`/opt/trade/.venv`。
- 独立服务账户：`trade`；新数据根：`/var/lib/trade-rebuild`，由该账户独占写入。原软件目录保持原样。
- 应用服务模板：[trade-rebuild.service](../deploy/rebuild/trade-rebuild.service)。使用 `--no-proxy-headers` 保留真实连接方，不信任任意转发 IP。
- HTTPS 入口模板：[Caddyfile](../deploy/rebuild/Caddyfile)；Caddy 的 systemd drop-in：[caddy-trade.conf](../deploy/rebuild/caddy-trade.conf)。管理员核对后放到对应系统配置位置。
- 服务端通过固定目录配置，不开放桌面原生目录窗口或远程关停启动器。备份下载、恢复预检和复制功能继续提供；数据根切换由管理员修改环境配置并重启服务完成。

构建前执行前端构建与测试，再安装后端 requirements；不要混用旧 `app.main`。不让 Caddy 或静态文件服务直接读取数据库、备份和源文件。

## 管理员配置

以下为字段示例，必须替换示例值。环境文件权限应限制为管理员读取（systemd 在启动服务前读取），不得进入 Git 或备份包。

`/etc/trade/rebuild.env`：

```ini
TRADE_PUBLIC_ORIGIN=https://trade.example.com
TRADE_PROXY_TOKEN=REPLACE_WITH_RANDOM_TOKEN
```

`/etc/trade/proxy.env`：

```ini
TRADE_PUBLIC_ORIGIN=https://trade.example.com
TRADE_PROXY_TOKEN=REPLACE_WITH_THE_SAME_RANDOM_TOKEN
TRADE_LOGIN_USER=your_login_name
TRADE_LOGIN_PASSWORD_HASH='REPLACE_WITH_CADDY_PASSWORD_HASH'
```

用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成独立代理令牌，两处保持一致。用 `caddy hash-password` 交互生成登录密码哈希；不把明文密码放进命令行。代理令牌不作为浏览器登录密码。远程 origin 只允许一个 HTTPS DNS 地址，不接受通配符、路径、查询参数或嵌入凭据。

有实际域名与有效配置后，管理员先用 Caddy 的 `validate` 命令在相同环境变量下验证配置，再启用服务。应用模板固定端口 8011；更换端口时同时修改 systemd、应用 `TRADE_REBUILD_PORT` 与 Caddy upstream。应用端口不开放公网。仅有代理令牌不等于用户认证：必须保留 Caddy 全站 `basic_auth`，不能只给 `/api` 加认证而放行静态页或会话入口。

## 发布验收

1. 外部浏览器匿名访问首页、`/health`、`/api/v1/session` 均要求登录；错误密码返回401。
2. 正确登录后经 HTTPS 加载页面，应用会话 cookie 同时有 Secure / HttpOnly / SameSite=Strict。
3. 无 CSRF 的写入、其他网站 Origin、伪造代理令牌、非 loopback 直连被拒绝。
4. 验证中文流式 AI、PDF下载、完整备份恢复、重启恢复任务；代理不可缓存账户/API响应或缓冲整段 AI 输出。
5. 核对域名证书、日志权限、磁盘余量和备份恢复。应用本机 CLI 在服务器上连接 loopback，凭据不进入任务文件。

此模式是一个受控用户空间，登录账户均访问同一个 Trade 数据根；没有多租户隔离或每账户权限系统。

## 配置依据

本机集成验证使用 `scripts/smoke_rebuild_proxy.py --caddy <Caddy可执行文件>`：隔离随机端口、一天有效的自签名证书、curl显式信任该证书，不改系统信任或 hosts。实际检查匿名/错误密码401、Secure会话、CSRF/跨站拒绝、代理覆盖浏览器伪造内部头、认证后账户创建读取及静态首页。完成后停止所拥有的测试进程。该测试不等于真实公网域名、Linux服务或外部AI流验收。

- [Caddy Basic Authentication](https://caddyserver.com/docs/caddyfile/directives/basic_auth)：全路径登录、哈希密码、HTTP 401。
- [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)：覆盖 upstream headers 与流式刷新。
- [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https)：实际证书管理由部署环境和域名配置决定。
