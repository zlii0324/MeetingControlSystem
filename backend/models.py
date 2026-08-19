from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Scheduled', 'Running', 'Finished', 'Cancelled')",
            name="ck_meetings_status",
        ),
        Index("idx_meetings_room_id", "room_id"),
        Index("idx_meetings_status", "status"),
        Index("idx_meetings_start_time", "start_time"),
        Index("idx_meetings_series_id", "series_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    host_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mail_owner: Mapped[str | None] = mapped_column(String(320))
    start_time: Mapped[str] = mapped_column(String(32), nullable=False)
    end_time: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    password_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    max_occupants: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    lobby_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meeting_url: Mapped[str] = mapped_column(Text, nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(64))
    recurrence_type: Mapped[str | None] = mapped_column(String(20))
    recurrence_interval: Mapped[int | None] = mapped_column(Integer)
    recurrence_count: Mapped[int | None] = mapped_column(Integer)
    recurrence_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))
    cancelled_at: Mapped[str | None] = mapped_column(String(32))

    attendees: Mapped[list["MeetingAttendee"]] = relationship(
        back_populates="meeting",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="MeetingAttendee.sort_order",
    )
    access_logs: Mapped[list["AccessLog"]] = relationship(
        back_populates="meeting",
        passive_deletes=True,
    )


class AccessLog(Base):
    __tablename__ = "access_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    join_time: Mapped[str] = mapped_column(String(32), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(Text)

    meeting: Mapped[Meeting | None] = relationship(back_populates="access_logs")


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"
    __table_args__ = (Index("idx_meeting_attendees_meeting_id", "meeting_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    meeting: Mapped[Meeting] = relationship(back_populates="attendees")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'scheduler')", name="ck_users_role"),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'disabled')",
            name="ck_users_status",
        ),
        Index("idx_users_status", "status"),
        Index("idx_users_role", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    job_title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    phone_number: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    email_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    custom_theme_color: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    register_message: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[str | None] = mapped_column(String(32))
    rejected_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(String(32))

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("idx_user_sessions_user_id", "user_id"),
        Index("idx_user_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(32))
    created_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class PasswordResetRequest(Base):
    __tablename__ = "password_reset_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_password_reset_requests_status",
        ),
        Index("idx_password_reset_requests_status", "status"),
        Index("idx_password_reset_requests_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_at: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[str | None] = mapped_column(String(32))


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("idx_password_reset_tokens_user_id", "user_id"),
        Index("idx_password_reset_tokens_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    used_at: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CalendarSubscriptionToken(Base):
    __tablename__ = "calendar_subscription_tokens"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


case_insensitive_group_name = (
    String(80)
    .with_variant(String(80, collation="NOCASE"), "sqlite")
    .with_variant(String(80, collation="utf8mb4_unicode_ci"), "mysql")
)


class UserGroup(Base):
    __tablename__ = "user_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        case_insensitive_group_name, nullable=False, unique=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class UserGroupMember(Base):
    __tablename__ = "user_group_members"
    __table_args__ = (
        CheckConstraint(
            "member_role IN ('admin', 'member')", name="ck_user_group_members_role"
        ),
        Index("idx_user_group_members_user_id", "user_id"),
        Index("idx_user_group_members_group_id", "group_id"),
    )

    group_id: Mapped[int] = mapped_column(
        ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    member_role: Mapped[str] = mapped_column(String(20), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class Milestone(Base):
    __tablename__ = "milestones"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'in_progress', 'completed')",
            name="ck_milestones_status",
        ),
        Index("idx_milestones_due_date", "due_date"),
        Index("idx_milestones_status", "status"),
        Index("idx_milestones_created_by", "created_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    due_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class MilestoneRelatedUser(Base):
    __tablename__ = "milestone_related_users"
    __table_args__ = (Index("idx_milestone_related_users_user_id", "user_id"),)

    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("milestones.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class MilestonePin(Base):
    __tablename__ = "milestone_pins"
    __table_args__ = (Index("idx_milestone_pins_user_id", "user_id"),)

    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("milestones.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


def model_to_dict(model: Base | None) -> dict[str, Any] | None:
    if model is None:
        return None
    mapper = inspect(model).mapper
    return {attribute.key: getattr(model, attribute.key) for attribute in mapper.column_attrs}
