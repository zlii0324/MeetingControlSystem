# Meeting Control System

[English](#meeting-control-system) | [中文](#会议管理系统)

Meeting Control System is a meeting management MVP built around Jitsi Reservation. It includes:

- A Flask backend API
- SQLAlchemy 2 ORM with MySQL support and an automatic SQLite fallback when MySQL is not configured
- A React and Ant Design single-page calendar interface
- One-time meeting password generation and storage
- Meeting creation, viewing, editing, deletion, and cancellation
- Attendee list management
- Email invitations for attendees (SMTP configuration required)
- Administrator sign-in, user registration requests, and administrator approval
- Password recovery through one-time email links and administrator-assisted temporary password resets
- A reserved Jitsi Reservation `/conference` endpoint; Prosody configuration is not required at this stage

The default Jitsi meeting URL is:

```text
https://meet.wusupower.com/
```

For detailed deployment and usage instructions, see the [User Guide (Chinese)](./docs/操作文档.md).

## Quick Start

Linux, Windows, and macOS are supported. Python 3.11+ and Node.js 20+ are recommended.

### Backend

Linux / macOS:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
touch .env
python manage.py create-admin
# Create at least two separate administrator accounts to avoid losing all access
# if one administrator forgets their password.
python app.py
```

Windows PowerShell:

```powershell
cd backend
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
New-Item .env -ItemType File -Force
py manage.py create-admin
py app.py
```

By default, the backend runs at:

```text
http://0.0.0.0:5001
```

By default, the application uses the SQLite file specified by `DATABASE_PATH`. To use MySQL in production, set `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USERNAME`, and `DATABASE_PASSWORD` in `backend/.env`. If all of these primary MySQL settings are left empty, the application automatically falls back to SQLite. When connecting to an empty MySQL database for the first time, SQLAlchemy creates the required tables and indexes.

### Frontend

Linux / macOS:

```bash
cd frontend
npm install
touch .env
npm run dev
```

Windows PowerShell:

```powershell
cd frontend
npm install
New-Item .env -ItemType File -Force
npm run dev
```

By default, the frontend development server runs at:

```text
http://127.0.0.1:5173
```

---

# 会议管理系统

[English](#meeting-control-system) | [中文](#会议管理系统)

这是一个基于 Jitsi Reservation 的会议管理 MVP，包含：

- Flask 后端 API
- SQLAlchemy 2 ORM，支持 MySQL，并在未配置 MySQL 时自动使用 SQLite
- React + Ant Design 单页日历 UI
- 一次性会议密码生成与保存
- 会议创建、查看、编辑、删除/取消
- 参会者名单管理
- 参会者邮件邀请通知（需配置 SMTP）
- 管理端账号登录、用户注册申请与管理员审核
- 邮件一次性链接找回密码，以及管理员协助的临时密码重置
- 预留 Jitsi Reservation `/conference` 接口，当前阶段不用配置 Prosody

当前 Jitsi 会议地址默认使用：

```text
https://meet.wusupower.com/
```

详细部署与使用说明见 [操作文档](./docs/操作文档.md)。

## 快速启动

支持 Linux、Windows 和 macOS。建议使用 Python 3.11+ 和 Node.js 20+。

### 后端

Linux / macOS：

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

Windows PowerShell：

```powershell
cd backend
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
New-Item .env -ItemType File -Force
py manage.py create-admin
py app.py
```

后端默认运行在：

```text
http://0.0.0.0:5001
```

数据库默认使用 `DATABASE_PATH` 指向的 SQLite 文件。生产环境如需 MySQL，填写
`backend/.env` 中的 `DATABASE_HOST`、`DATABASE_NAME`、`DATABASE_USERNAME` 和
`DATABASE_PASSWORD`；这些 MySQL 主配置全部留空时会自动回退到 SQLite。首次连接空的
MySQL 数据库时，SQLAlchemy 会创建所需表和索引。

### 前端

Linux / macOS：

```bash
cd frontend
npm install
touch .env
npm run dev
```

Windows PowerShell：

```powershell
cd frontend
npm install
New-Item .env -ItemType File -Force
npm run dev
```

前端开发服务默认运行在：

```text
http://127.0.0.1:5173
```
