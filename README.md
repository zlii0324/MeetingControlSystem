# 会议管理系统

这是一个基于 Jitsi Reservation 的会议管理 MVP，包含：

- Flask 后端 API
- SQLite 数据库
- React + Ant Design 单页日历 UI
- 一次性会议密码生成与保存
- 会议创建、查看、编辑、删除/取消
- 参会者名单管理
- 参会者邮件邀请通知（需配置 SMTP）
- 管理端账号登录、用户注册申请与管理员审核
- 管理员用户管理、密码找回申请与临时密码重置
- 预留 Jitsi Reservation `/conference` 接口，当前阶段不用配置 Prosody

当前 Jitsi 会议地址默认使用：

```text
https://meet.wusupower.com/
```

详细部署与使用说明见 [操作文档](./docs/操作文档.md)。

## 快速启动

### Docker 三服务测试环境

已提供一套仅绑定本机的测试环境，包含应用服务器、Mailpit 测试邮件服务器和官方 Jitsi 组件。先启动 Docker Desktop，然后运行：

```bash
./scripts/start-test-stack.sh
./scripts/smoke-test-stack.py
```

访问入口：

- 应用：<http://localhost:8080>
- 测试邮件收件箱：<http://localhost:8025>
- Jitsi：<http://localhost:8000>
- 测试管理员：`admin@example.test` / `TestAdmin123`

Jitsi 测试入口使用仅绑定本机的 HTTP，因此不需要安装测试证书。邮件只会被 Mailpit 捕获，不会发到公网。这套配置关闭了 Jitsi 登录并使用固定测试密码，只适合本机开发测试，不能直接作为生产配置；生产环境必须使用正式域名和 HTTPS。

停止服务但保留测试数据库：

```bash
docker compose -f compose.test.yml down
```

停止服务并清空全部 Docker 测试数据：

```bash
docker compose -f compose.test.yml down --volumes
```

### 后端

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
touch .env
python manage.py create-admin
# 建议至少创建两个不同管理员账号，避免单人遗失密码导致无人可登录。
python app.py
```

后端默认运行在：

```text
http://0.0.0.0:5001
```

### 前端

```bash
cd frontend
npm install
touch .env
npm run dev
```

前端开发服务默认运行在：

```text
http://127.0.0.1:5173
```
