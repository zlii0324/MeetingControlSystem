from __future__ import annotations

import argparse
import getpass
import sys
from urllib.parse import quote, urlencode

from auth import AuthError, create_admin_user, normalize_username, reset_user_password
from database import init_db
from config import config
from jitsi_auth import JitsiJwtError, create_jitsi_token


def prompt_required(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print(f"{label}不能为空")


def prompt_password() -> str:
    while True:
        password = getpass.getpass("密码: ")
        confirm = getpass.getpass("确认密码: ")
        if password != confirm:
            print("两次输入的密码不一致")
            continue
        return password


def create_admin(args: argparse.Namespace) -> int:
    init_db()

    username = args.username or prompt_required("用户名")
    display_name = args.display_name or prompt_required("用户昵称（真实姓名）", username)
    email = args.email or prompt_required("邮箱")
    job_title = args.job_title or ""
    phone_number = args.phone_number or ""
    password = args.password or prompt_password()

    if args.if_not_exists:
        try:
            normalized_username = normalize_username(username)
        except AuthError as exc:
            print(f"用户名无效：{exc}", file=sys.stderr)
            return 1

        from database import get_connection

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT username FROM users WHERE username = ?",
                (normalized_username,),
            ).fetchone()
        if existing is not None:
            print(f"管理员已存在：{existing['username']}")
            return 0

    try:
        user = create_admin_user(
            username=username,
            display_name=display_name,
            email=email,
            password=password,
            job_title=job_title,
            phone_number=phone_number,
        )
    except AuthError as exc:
        print(f"创建失败：{exc}", file=sys.stderr)
        return 1

    print(f"管理员已创建：{user['username']}")
    return 0


def reset_password(args: argparse.Namespace) -> int:
    init_db()

    try:
        username = normalize_username(args.username)
    except AuthError as exc:
        print(f"用户名无效：{exc}", file=sys.stderr)
        return 1

    from database import get_connection

    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            print("用户不存在", file=sys.stderr)
            return 1

    try:
        result = reset_user_password(int(row["id"]))
    except AuthError as exc:
        print(f"重置失败：{exc}", file=sys.stderr)
        return 1

    print(f"用户已重置：{result['user']['username']}")
    print(f"临时密码：{result['temporaryPassword']}")
    return 0


def generate_jitsi_token(args: argparse.Namespace) -> int:
    room = args.room.strip()
    try:
        token = create_jitsi_token(
            room,
            display_name=args.display_name,
            email=args.email,
            user_id=args.user_id,
        )
    except JitsiJwtError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1

    room_url = f"{config.normalized_jitsi_base_url}{quote(room, safe='')}"
    print(f"JWT：{token}")
    print(f"入会地址：{room_url}?{urlencode({'jwt': token})}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="会议管理系统维护命令")
    subparsers = parser.add_subparsers(dest="command")

    create_admin_parser = subparsers.add_parser("create-admin", help="创建管理员账号")
    create_admin_parser.add_argument("--username", help="用户名；省略时交互输入")
    create_admin_parser.add_argument("--display-name", help="用户昵称（真实姓名）；省略时交互输入")
    create_admin_parser.add_argument("--email", help="邮箱；省略时交互输入")
    create_admin_parser.add_argument("--job-title", help="职称；省略时留空")
    create_admin_parser.add_argument("--phone-number", help="电话号码；省略时留空")
    create_admin_parser.add_argument("--password", help="密码；省略时安全交互输入")
    create_admin_parser.add_argument(
        "--if-not-exists",
        action="store_true",
        help="同名用户已存在时正常退出，便于重复执行初始化",
    )
    create_admin_parser.set_defaults(func=create_admin)

    reset_password_parser = subparsers.add_parser("reset-password", help="重置指定用户密码")
    reset_password_parser.add_argument("username", help="用户名")
    reset_password_parser.set_defaults(func=reset_password)

    jitsi_token_parser = subparsers.add_parser(
        "generate-jitsi-token",
        help="生成仅允许进入指定房间的 Jitsi JWT",
    )
    jitsi_token_parser.add_argument("room", help="Jitsi 房间名")
    jitsi_token_parser.add_argument("--display-name", help="写入 token 的参会者显示名称")
    jitsi_token_parser.add_argument("--email", help="写入 token 的参会者邮箱")
    jitsi_token_parser.add_argument("--user-id", help="写入 token 的业务用户 ID")
    jitsi_token_parser.set_defaults(func=generate_jitsi_token)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
