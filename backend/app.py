from __future__ import annotations

from flask import Flask, jsonify, request
from flask_cors import CORS

from config import config
from database import init_db
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


def create_app() -> Flask:
    app = Flask(__name__)
    init_db()

    if config.enable_cors:
        CORS(app, resources={r"/api/*": {"origins": config.frontend_origin}})

    @app.errorhandler(MeetingError)
    def handle_meeting_error(error: MeetingError):
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
                "jitsiBaseUrl": config.normalized_jitsi_base_url,
            }
        )

    @app.get("/api/meetings")
    def meetings_index():
        return jsonify({"items": list_meetings(request.args.get("status"))})

    @app.post("/api/meetings")
    def meetings_create():
        payload = request.get_json(silent=True) or {}
        return jsonify(create_meeting(payload)), 201

    @app.get("/api/meetings/<int:meeting_id>")
    def meetings_show(meeting_id: int):
        return jsonify(get_meeting(meeting_id))

    @app.put("/api/meetings/<int:meeting_id>")
    def meetings_update(meeting_id: int):
        payload = request.get_json(silent=True) or {}
        return jsonify(update_meeting(meeting_id, payload))

    @app.delete("/api/meetings/<int:meeting_id>")
    def meetings_delete(meeting_id: int):
        return jsonify(delete_meeting(meeting_id))

    @app.get("/api/access-logs")
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
    app.run(host="0.0.0.0", port=5001, debug=True)
