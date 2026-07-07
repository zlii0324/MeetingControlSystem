# 会议管理系统

这是一个基于 Jitsi Reservation 的会议管理 MVP，包含：

- Flask 后端 API
- SQLite 数据库
- React + Ant Design 单页日历 UI
- 一次性会议密码生成与保存
- 会议创建、查看、编辑、删除/取消
- 参会者名单管理
- 预留 Jitsi Reservation `/conference` 接口，当前阶段不用配置 Prosody

当前 Jitsi 会议地址默认使用：

```text
https://meet.wusupower.com/
```

详细部署与使用说明见 [操作文档](./docs/操作文档.md)。

## 快速启动

### 后端

```bash
cd backend
source ../venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

后端默认运行在：

```text
http://localhost:5001
```

### 前端

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

前端默认运行在：

```text
http://localhost:5173
```
