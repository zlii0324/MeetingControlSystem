from __future__ import annotations

import os
from functools import wraps

from flask import Flask, g, jsonify, request
from flask_cors import CORS

from calendar_feed import (
    CalendarFeedError,
    build_personal_calendar,
    ensure_calendar_subscription,
)

from auth import (
    AuthError,
    approve_password_reset_request,
    approve_user,
    create_user,
    delete_user as delete_system_user,
    get_authenticated_user,
    get_user,
    list_password_reset_requests,
    list_users as list_system_users,
    login_user,
    logout_token,
    reject_password_reset_request,
    register_user,
    reject_user,
    request_email_password_reset,
    request_password_reset,
    reset_password_with_email_token,
    reset_user_password,
    search_user_directory,
    update_user as update_system_user,
    update_own_profile,
    update_own_preferences,
    change_own_password,
)
from config import config
from database import init_db
from jitsi_auth import JitsiJwtError, verify_jitsi_token
from groups import (
    GroupError,
    add_group_member,
    create_group,
    delete_group,
    get_group,
    list_groups,
    remove_group_member,
    update_group,
    update_group_member,
)
from milestones import (
    MilestoneError,
    create_milestone,
    delete_milestone,
    get_milestone,
    list_milestones,
    update_milestone,
    update_milestone_pin,
)
from services import (
    MeetingError,
    allocate_conference,
    create_meeting,
    delete_meeting,
    finish_conference,
    get_meeting,
    get_meeting_for_reservation,
    list_access_logs,
    list_meetings,
    public_join_meeting,
    reservation_payload,
    update_meeting,
    verify_join_password,
)


def current_session_token() -> str | None:
    return request.cookies.get(config.session_cookie_name)


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        g.current_user = get_authenticated_user(current_session_token())
        return function(*args, **kwargs)

    return wrapper


def admin_required(function):
    @wraps(function)
    @login_required
    def wrapper(*args, **kwargs):
        if g.current_user["role"] != "admin":
            raise AuthError("需要管理员权限", 403)
        return function(*args, **kwargs)

    return wrapper


def create_app() -> Flask:
    app = Flask(__name__)
    init_db()

    if config.enable_cors:
        if config.frontend_origins:
            CORS(
                app,
                resources={r"/api/*": {"origins": list(config.frontend_origins)}},
                supports_credentials=True,
            )
        else:
            CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

    @app.errorhandler(MeetingError)
    def handle_meeting_error(error: MeetingError):
        return jsonify({"message": str(error)}), error.status_code

    @app.errorhandler(AuthError)
    def handle_auth_error(error: AuthError):
        return jsonify({"message": str(error)}), error.status_code

    @app.errorhandler(GroupError)
    def handle_group_error(error: GroupError):
        return jsonify({"message": str(error)}), error.status_code

    @app.errorhandler(CalendarFeedError)
    def handle_calendar_feed_error(error: CalendarFeedError):
        return jsonify({"message": str(error)}), error.status_code

    @app.errorhandler(MilestoneError)
    def handle_milestone_error(error: MilestoneError):
        return jsonify({"message": str(error)}), error.status_code

    @app.errorhandler(404)
    def handle_not_found(_error):
        return jsonify({"message": "接口不存在"}), 404

    @app.errorhandler(500)
    def handle_internal_error(_error):
        return jsonify({"message": "服务器内部错误"}), 500

    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "status": "ok",
                "databaseBackend": config.database_backend,
                "jitsiBaseUrl": config.normalized_jitsi_base_url,
                "jitsiJwtEnabled": config.jitsi_jwt_enabled,
            }
        )

    @app.get("/internal/jitsi/token/validate")
    def validate_jitsi_token():
        try:
            verify_jitsi_token(
                request.headers.get("X-Jitsi-Token", ""),
                request.headers.get("X-Jitsi-Room", ""),
            )
        except JitsiJwtError:
            # Nginx converts this denial to a public 404 response.
            return "", 403
        return "", 204

    @app.post("/api/auth/register")
    def auth_register():
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": register_user(payload)}), 201

    @app.post("/api/auth/password-reset-requests")
    def auth_password_reset_request():
        payload = request.get_json(silent=True) or {}
        return jsonify({"request": request_password_reset(payload)}), 201

    @app.post("/api/auth/email-password-reset")
    def auth_email_password_reset_request():
        payload = request.get_json(silent=True) or {}
        return jsonify(request_email_password_reset(payload)), 202

    @app.post("/api/auth/reset-password")
    def auth_reset_password():
        payload = request.get_json(silent=True) or {}
        return jsonify(reset_password_with_email_token(payload))

    @app.post("/api/auth/login")
    def auth_login():
        payload = request.get_json(silent=True) or {}
        result = login_user(
            payload=payload,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=request.headers.get("User-Agent"),
        )
        response = jsonify({"user": result["user"], "expiresAt": result["expiresAt"]})
        response.set_cookie(
            config.session_cookie_name,
            result["token"],
            max_age=max(1, config.session_ttl_days) * 24 * 60 * 60,
            httponly=True,
            secure=config.session_cookie_secure,
            samesite=config.session_cookie_samesite,
            path="/",
        )
        return response

    @app.post("/api/auth/logout")
    def auth_logout():
        logout_token(current_session_token())
        response = jsonify({"ok": True})
        response.delete_cookie(
            config.session_cookie_name,
            path="/",
            secure=config.session_cookie_secure,
            samesite=config.session_cookie_samesite,
        )
        return response

    @app.get("/api/auth/me")
    @login_required
    def auth_me():
        return jsonify({"user": g.current_user})

    @app.patch("/api/auth/preferences")
    @login_required
    def auth_preferences_update():
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": update_own_preferences(g.current_user["id"], payload)})

    @app.patch("/api/auth/profile")
    @login_required
    def auth_profile_update():
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": update_own_profile(g.current_user["id"], payload)})
    
    @app.post("/api/auth/change-password")
    @login_required
    def auth_change_password():
        payload = request.get_json(silent=True) or {}
        change_own_password(g.current_user["id"], payload)

        response = jsonify({"ok": True})
        response.delete_cookie(
            config.session_cookie_name,
            path="/",
            secure=config.session_cookie_secure,
            samesite=config.session_cookie_samesite,
        )
        return response

    @app.get("/api/users/directory")
    @login_required
    def users_directory():
        return jsonify({"items": search_user_directory(request.args.get("q"))})

    @app.get("/api/groups")
    @login_required
    def groups_index():
        return jsonify({"items": list_groups(g.current_user)})

    @app.post("/api/groups")
    @login_required
    def groups_create():
        payload = request.get_json(silent=True) or {}
        return jsonify({"group": create_group(payload, g.current_user)}), 201

    @app.get("/api/groups/<int:group_id>")
    @login_required
    def groups_show(group_id: int):
        return jsonify({"group": get_group(group_id, g.current_user)})

    @app.patch("/api/groups/<int:group_id>")
    @login_required
    def groups_update(group_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"group": update_group(group_id, payload, g.current_user)})

    @app.delete("/api/groups/<int:group_id>")
    @login_required
    def groups_delete(group_id: int):
        return jsonify(delete_group(group_id, g.current_user))

    @app.post("/api/groups/<int:group_id>/members")
    @login_required
    def group_members_create(group_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"group": add_group_member(group_id, payload, g.current_user)}), 201

    @app.patch("/api/groups/<int:group_id>/members/<int:user_id>")
    @login_required
    def group_members_update(group_id: int, user_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"group": update_group_member(group_id, user_id, payload, g.current_user)})

    @app.delete("/api/groups/<int:group_id>/members/<int:user_id>")
    @login_required
    def group_members_delete(group_id: int, user_id: int):
        return jsonify({"group": remove_group_member(group_id, user_id, g.current_user)})

    @app.get("/api/admin/users")
    @admin_required
    def admin_users_index():
        return jsonify({"items": list_system_users(request.args.get("status"))})

    @app.post("/api/admin/users")
    @admin_required
    def admin_users_create():
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": create_user(payload)}), 201

    @app.get("/api/admin/users/<int:user_id>")
    @admin_required
    def admin_users_show(user_id: int):
        return jsonify({"user": get_user(user_id)})

    @app.patch("/api/admin/users/<int:user_id>")
    @admin_required
    def admin_users_update(user_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": update_system_user(user_id, payload)})

    @app.delete("/api/admin/users/<int:user_id>")
    @admin_required
    def admin_users_delete(user_id: int):
        return jsonify(delete_system_user(user_id, g.current_user["id"]))

    @app.post("/api/admin/users/<int:user_id>/reset-password")
    @admin_required
    def admin_users_reset_password(user_id: int):
        return jsonify(reset_user_password(user_id))

    @app.post("/api/admin/users/<int:user_id>/approve")
    @admin_required
    def admin_users_approve(user_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"user": approve_user(user_id, g.current_user["id"], payload)})

    @app.post("/api/admin/users/<int:user_id>/reject")
    @admin_required
    def admin_users_reject(user_id: int):
        return jsonify({"user": reject_user(user_id)})

    @app.get("/api/admin/password-reset-requests")
    @admin_required
    def admin_password_reset_requests_index():
        return jsonify({"items": list_password_reset_requests(request.args.get("status"))})

    @app.post("/api/admin/password-reset-requests/<int:request_id>/approve")
    @admin_required
    def admin_password_reset_requests_approve(request_id: int):
        return jsonify(approve_password_reset_request(request_id, g.current_user["id"]))

    @app.post("/api/admin/password-reset-requests/<int:request_id>/reject")
    @admin_required
    def admin_password_reset_requests_reject(request_id: int):
        return jsonify({"request": reject_password_reset_request(request_id, g.current_user["id"])})

    @app.get("/api/meetings")
    @login_required
    def meetings_index():
        return jsonify({"items": list_meetings(g.current_user, request.args.get("status"))})

    @app.post("/api/calendar/subscription")
    @login_required
    def calendar_subscription_create():
        return jsonify(ensure_calendar_subscription(g.current_user))

    @app.get("/api/calendar/subscriptions/<token>.ics")
    def calendar_subscription_feed(token: str):
        calendar_body, _calendar_name = build_personal_calendar(token)
        response = app.response_class(calendar_body, content_type="text/calendar; charset=utf-8")
        response.headers["Content-Disposition"] = 'inline; filename="personal-meetings.ics"'
        response.headers["Cache-Control"] = "private, max-age=300"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/api/meetings")
    @login_required
    def meetings_create():
        payload = request.get_json(silent=True) or {}
        payload.setdefault("mailOwner", g.current_user["email"])
        return jsonify(create_meeting(payload, g.current_user)), 201

    @app.get("/api/meetings/<int:meeting_id>")
    @login_required
    def meetings_show(meeting_id: int):
        return jsonify(get_meeting(meeting_id))

    @app.put("/api/meetings/<int:meeting_id>")
    @login_required
    def meetings_update(meeting_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify(
            update_meeting(meeting_id, payload, g.current_user, request.args.get("scope"))
        )

    @app.delete("/api/meetings/<int:meeting_id>")
    @login_required
    def meetings_delete(meeting_id: int):
        return jsonify(delete_meeting(meeting_id, request.args.get("scope")))

    @app.get("/api/milestones")
    @login_required
    def milestones_index():
        return jsonify({"items": list_milestones(g.current_user, request.args.get("status"))})

    @app.post("/api/milestones")
    @login_required
    def milestones_create():
        payload = request.get_json(silent=True) or {}
        return jsonify({"milestone": create_milestone(payload, g.current_user)}), 201

    @app.get("/api/milestones/<int:milestone_id>")
    @login_required
    def milestones_show(milestone_id: int):
        return jsonify({"milestone": get_milestone(milestone_id, g.current_user)})

    @app.patch("/api/milestones/<int:milestone_id>")
    @login_required
    def milestones_update(milestone_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify({"milestone": update_milestone(milestone_id, payload, g.current_user)})

    @app.patch("/api/milestones/<int:milestone_id>/pin")
    @login_required
    def milestones_pin_update(milestone_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify(
            {
                "milestone": update_milestone_pin(
                    milestone_id,
                    payload.get("pinned"),
                    g.current_user,
                )
            }
        )

    @app.delete("/api/milestones/<int:milestone_id>")
    @login_required
    def milestones_delete(milestone_id: int):
        return jsonify(delete_milestone(milestone_id, g.current_user))

    @app.get("/api/access-logs")
    @login_required
    def access_logs_index():
        limit = int(request.args.get("limit", 100))
        return jsonify({"items": list_access_logs(limit)})

    @app.get("/api/public/meetings/<room_id>")
    def public_meetings_show(room_id: str):
        return jsonify(public_join_meeting(room_id))

    @app.post("/api/public/meetings/<room_id>/verify")
    def public_meetings_verify(room_id: str):
        payload = request.get_json(silent=True) or {}
        return jsonify(
            verify_join_password(
                room_id=room_id,
                password=payload.get("password"),
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
                user_agent=request.headers.get("User-Agent"),
                display_name=payload.get("displayName"),
                email=payload.get("email"),
            )
        )

    @app.post("/conference")
    def conference_create():
        room_name = request.form.get("name", "")
        mail_owner = request.form.get("mail_owner") or None
        status_code, payload = allocate_conference(
            room_name=room_name,
            mail_owner=mail_owner,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=request.headers.get("User-Agent"),
        )
        return jsonify(payload), status_code

    @app.get("/conference/<int:meeting_id>")
    def conference_show(meeting_id: int):
        meeting = get_meeting_for_reservation(meeting_id)
        return jsonify(reservation_payload(meeting))

    @app.delete("/conference/<int:meeting_id>")
    def conference_delete(meeting_id: int):
        return jsonify(finish_conference(meeting_id))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=False)
