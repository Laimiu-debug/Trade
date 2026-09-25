# Final Trade - 快速启动指南

## 启动步骤

### 1. 启动后端服务

```bash
# 进入后端目录
cd backend

# 启动开发服务器
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**验证后端是否启动成功**:
访问 http://127.0.0.1:8000/health 或 http://127.0.0.1:8000/api/config

### 2. 启动前端服务

```bash
# 进入前端目录
cd frontend

# 首次运行需要安装依赖
npm install

# 启动开发服务器
npm run dev
```

**前端访问地址**: http://localhost:4173

### 3. 访问应用

打开浏览器访问: **http://localhost:4173**

## 常见问题

### 问题1: 网页打不开

**可能原因**:
1. 后端服务未启动
2. 前端服务未启动
3. 端口被占用

**解决方法**:

1. **检查后端**:
```bash
# 测试后端API
curl http://127.0.0.1:8000/api/config
```

2. **检查前端**:
```bash
# 查看前端日志
cd frontend
npm run dev
```

3. **更换端口** (如果端口被占用):
```bash
# 后端更换端口
python -m uvicorn app.main:app --reload --port 8001

# 前端更换端口
cd frontend
npm run dev -- --port 4174
```

### 问题2: API请求失败

**检查前端配置**:
```bash
# 查看前端API配置
cat frontend/src/shared/api/client.ts
```

确保API_BASE_URL指向正确的后端地址:
```typescript
const API_BASE_URL = 'http://127.0.0.1:8000';
```

### 问题3: 数据加载失败

**检查数据源配置**:
```bash
# 查看当前配置
curl http://127.0.0.1:8000/api/config
```

确认 `tdx_data_path` 是否正确配置。

## 完整重启流程

如果遇到问题，按以下步骤完全重启:

```bash
# 1. 停止所有服务 (Ctrl+C)

# 2. 清理前端缓存
cd frontend
rm -rf node_modules/.vite

# 3. 重启后端
cd ../backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 4. 重启前端 (新终端)
cd frontend
npm run dev

# 5. 清除浏览器缓存并刷新
# 按 Ctrl+Shift+Delete 清除缓存
# 或使用无痕模式访问
```

## 验证服务状态

### 后端健康检查
```bash
curl http://127.0.0.1:8000/health
```

### 获取配置信息
```bash
curl http://127.0.0.1:8000/api/config
```

### 查看存储状态
```bash
curl http://127.0.0.1:8000/api/system/storage
```

## 生产环境部署

### 构建前端
```bash
cd frontend
npm run build
```

### 使用预览服务器测试构建
```bash
npm run preview
```

预览服务器地址: http://localhost:4173

## 浏览器建议

推荐使用现代浏览器:
- Chrome 90+
- Firefox 88+
- Edge 90+

避免使用IE浏览器，不支持现代JavaScript特性。

## 获取帮助

如果仍有问题:
1. 查看 [ARCHITECTURE.md](./ARCHITECTURE.md) 了解架构
2. 查看 [VERIFICATION.md](./VERIFICATION.md) 了解验证步骤
3. 检查浏览器控制台 (F12) 查看错误信息
4. 检查后端终端日志查看API错误

## 开发模式特性

开发模式下启用:
- ✅ 热模块替换 (HMR)
- ✅ 源码映射 (Source Maps)
- ✅ 详细错误信息
- ✅ API请求日志

生产模式:
- ⚡ 优化构建
- 🗜️ 代码压缩
- 📦 代码分割
