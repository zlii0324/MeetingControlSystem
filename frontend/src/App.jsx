import React, { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Collapse,
  ConfigProvider,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Popover,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  DatePicker,
  message,
  theme as antdTheme,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import dayjs from "dayjs";
import {
  CalendarClock,
  CalendarPlus,
  Check,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Edit3,
  ExternalLink,
  Link2,
  Languages,
  LogOut,
  LockKeyhole,
  Mail,
  Monitor,
  Moon,
  Palette,
  Plus,
  RefreshCw,
  Repeat2,
  ShieldCheck,
  Sun,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
  Video,
  XCircle,
} from "lucide-react";
import {
  approvePasswordResetRequest,
  approveUser,
  addGroupMember,
  createGroup,
  createCalendarSubscription,
  createMeeting,
  createUser,
  deleteGroup,
  deleteMeeting,
  deleteUser,
  fetchCurrentUser,
  fetchGroups,
  fetchMeetings,
  fetchPasswordResetRequests,
  fetchPublicMeeting,
  fetchUsers,
  login,
  logout,
  registerAccount,
  rejectPasswordResetRequest,
  rejectUser,
  removeGroupMember,
  requestPasswordReset,
  requestEmailPasswordReset,
  resetPasswordWithToken,
  resetUserPassword,
  searchUserDirectory,
  updateUser,
  updateCurrentUserPreferences,
  updateGroup,
  updateGroupMember,
  updateMeeting,
  verifyMeetingPassword,
  changePassword,
} from "./api";
import { setLanguage, t } from "./i18n";

const { Text, Title } = Typography;
const PREFERENCES_STORAGE_KEY = "meeting-control-preferences";
const colorThemeOptions = [
  { value: "blue", label: "蓝色", color: "#2563eb" },
  { value: "orange", label: "橙色", color: "#ea580c" },
  { value: "teal", label: "青绿", color: "#0f8b8d" },
  { value: "violet", label: "紫色", color: "#7c3aed" },
  { value: "rose", label: "玫红", color: "#e11d48" },
  { value: "emerald", label: "翠绿", color: "#059669" },
  { value: "indigo", label: "靛蓝", color: "#4f46e5" },
];
const defaultPreferences = { colorTheme: "blue", appearanceMode: "light", language: "zh-CN" };
const weekLabels = ["一", "二", "三", "四", "五", "六", "日"];
const joinLinkOrigin = (import.meta.env.VITE_JOIN_LINK_ORIGIN || window.location.origin).replace(
  /\/+$/,
  "",
);

const statusMap = {
  Scheduled: { text: "已预约", color: "processing", className: "status-scheduled" },
  Running: { text: "进行中", color: "success", className: "status-running" },
  Finished: { text: "已结束", color: "default", className: "status-finished" },
  Cancelled: { text: "已取消", color: "error", className: "status-cancelled" },
};

const recurrenceTypeOptions = [
  { label: "每周", value: "weekly" },
  { label: "每两周", value: "biweekly" },
  { label: "每 N 天", value: "every_n_days" },
  { label: "每月", value: "monthly" },
];

const roleLabels = {
  admin: "管理员",
  scheduler: "员工",
};

function displayJobTitle(user, includeAdminSuffix = false) {
  const jobTitle = String(user?.jobTitle || "").trim() || t("未设置职称");
  return includeAdminSuffix && user?.role === "admin"
    ? t("{{jobTitle}}（管理员）", { jobTitle })
    : jobTitle;
}

const userStatusLabels = {
  pending: "待审核",
  active: "正常",
  rejected: "已拒绝",
  disabled: "已停用",
};

const userStatusColors = {
  pending: "processing",
  active: "success",
  rejected: "error",
  disabled: "default",
};

function loadPreferences() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(PREFERENCES_STORAGE_KEY) || "null");
    const colorTheme =
      saved?.colorTheme === "custom" ||
      colorThemeOptions.some((option) => option.value === saved?.colorTheme)
      ? saved.colorTheme
      : defaultPreferences.colorTheme;
    const appearanceMode = ["light", "dark", "system"].includes(saved?.appearanceMode)
      ? saved.appearanceMode
      : saved?.darkMode
        ? "dark"
        : defaultPreferences.appearanceMode;
    const language = saved?.language === "en-US" ? "en-US" : defaultPreferences.language;
    return { colorTheme, appearanceMode, language };
  } catch {
    return defaultPreferences;
  }
}

function toPayload(values) {
  const start = values.startTime;
  const end = values.endTime;
  const payload = {
    title: values.title,
    hostName: values.hostName,
    attendees: values.attendees || [],
    startTime: start?.toISOString(),
    endTime: end?.toISOString(),
    maxOccupants: values.maxOccupants,
    passwordRequired: Boolean(values.passwordRequired),
  };

  if (values.recurrenceEnabled) {
    payload.recurrence = {
      enabled: true,
      type: values.recurrenceType,
      interval: values.recurrenceInterval,
      count: values.recurrenceCount,
    };
  }

  return payload;
}

function toFormValues(meeting) {
  return {
    title: meeting.title,
    hostName: meeting.hostName,
    attendees: meeting.attendees || [],
    startTime: dayjs(meeting.startTime),
    endTime: dayjs(meeting.endTime),
    maxOccupants: meeting.maxOccupants,
    passwordRequired: meeting.passwordRequired,
  };
}

function displayTime(value) {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "-";
}

function normalizeIdentity(value) {
  return String(value || "").trim().toLocaleLowerCase();
}

function isMeetingAttendee(meeting, user) {
  if (!meeting || !user) return false;

  const email = normalizeIdentity(user.email);
  const username = normalizeIdentity(user.username);
  const displayName = normalizeIdentity(user.displayName);
  const userIdentifiers = new Set(
    [email, username, username ? `@${username}` : "", displayName].filter(Boolean),
  );

  return (meeting.attendees || []).some((attendee) =>
    userIdentifiers.has(normalizeIdentity(attendee)),
  );
}

function isEmailLike(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

function showMeetingSaveMessage(messageApi, successText, notification) {
  if (!notification?.requested) {
    messageApi.success(successText);
    return;
  }
  if (notification.status === "sent") {
    messageApi.success(
      t("{{success}}，已发送 {{sent}} 封通知邮件", {
        success: successText,
        sent: notification.sent,
      }),
    );
    return;
  }
  if (notification.status === "partial") {
    messageApi.warning(
      t("{{success}}，已发送 {{sent}}/{{requested}} 封通知邮件", {
        success: successText,
        sent: notification.sent,
        requested: notification.requested,
      }),
    );
    return;
  }
  if (notification.status === "disabled") {
    messageApi.warning(t("{{success}}，但邮件通知尚未配置", { success: successText }));
    return;
  }
  messageApi.warning(t("{{success}}，但通知邮件发送失败", { success: successText }));
}

function minutesBetween(start, end) {
  const minutes = dayjs(end).diff(dayjs(start), "minute");
  if (minutes < 60) return t("{{minutes}} 分钟", { minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest
    ? t("{{hours}} 小时 {{minutes}} 分钟", { hours, minutes: rest })
    : t("{{hours}} 小时", { hours });
}

function buildMeetingShareText(meeting, linkValue) {
  return [
    t("会议：{{title}}", { title: meeting.title }),
    t("会议时间：{{start}} - {{end}}", {
      start: displayTime(meeting.startTime),
      end: displayTime(meeting.endTime),
    }),
    t("主持人：{{host}}", { host: meeting.hostName }),
    meeting.createdCount
      ? t("周期会议：已生成 {{count}} 场", { count: meeting.createdCount })
      : null,
    t("会议链接：{{link}}", { link: linkValue }),
    meeting.password ? t("会议密码：{{password}}", { password: meeting.password }) : null,
  ]
    .filter(Boolean)
    .join("\n");
}

function absoluteUrl(value) {
  if (!value) return "";
  try {
    return new URL(String(value), joinLinkOrigin).toString();
  } catch {
    return String(value);
  }
}

function fallbackCopyText(value) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);

  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  try {
    const copied = document.execCommand("copy");
    if (!copied) {
      throw new Error("copy command failed");
    }
  } finally {
    document.body.removeChild(textarea);
  }
}

async function writeClipboardText(text) {
  const value = String(text ?? "").trim();
  if (!value) {
    throw new Error("nothing to copy");
  }

  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Fall through to the selection-based copy path for restricted browsers.
    }
  }

  fallbackCopyText(value);
}

function recurrenceText(meeting) {
  const recurrence = meeting?.recurrence;
  if (!recurrence) return t("非周期会议");

  const count = recurrence.count ? t("，共 {{count}} 场", { count: recurrence.count }) : "";
  const current =
    Number.isInteger(recurrence.index) && recurrence.count
      ? t("，当前第 {{index}} 场", { index: recurrence.index + 1 })
      : "";

  if (recurrence.type === "weekly") return t("每周重复{{count}}{{current}}", { count, current });
  if (recurrence.type === "biweekly") {
    return t("每两周重复{{count}}{{current}}", { count, current });
  }
  if (recurrence.type === "every_n_days") {
    return t("每 {{interval}} 天重复{{count}}{{current}}", {
      interval: recurrence.interval || 1,
      count,
      current,
    });
  }
  if (recurrence.type === "monthly") return t("每月重复{{count}}{{current}}", { count, current });
  return t("周期会议{{count}}{{current}}", { count, current });
}

function startOfMondayWeek(value) {
  return value.startOf("day").subtract((value.day() + 6) % 7, "day");
}

function buildWeekDates(value) {
  const start = startOfMondayWeek(value);
  return Array.from({ length: 7 }, (_, index) => start.add(index, "day"));
}

function buildMonthDates(value) {
  const firstDay = value.startOf("month");
  const gridStart = startOfMondayWeek(firstDay);
  return Array.from({ length: 42 }, (_, index) => gridStart.add(index, "day"));
}

function formatCalendarRange(value, view, language) {
  const localizedValue = value.locale(language === "en-US" ? "en" : "zh-cn");
  if (view === "month") {
    return localizedValue.format(t("YYYY年 M月"));
  }

  const week = buildWeekDates(localizedValue);
  const start = week[0];
  const end = week[6];
  if (start.isSame(end, "month")) {
    return `${start.format(t("YYYY年 M月D日"))} - ${end.format(t("D日"))}`;
  }
  if (start.isSame(end, "year")) {
    return `${start.format(t("YYYY年 M月D日"))} - ${end.format(t("M月D日"))}`;
  }
  return `${start.format(t("YYYY年 M月D日"))} - ${end.format(t("YYYY年 M月D日"))}`;
}

function MeetingFormModal({
  open,
  mode,
  initialValues,
  currentUser,
  groups = [],
  language,
  onCancel,
  onSubmit,
  loading,
}) {
  const [form] = Form.useForm();
  const [attendeeQuery, setAttendeeQuery] = useState("");
  const [directoryUsers, setDirectoryUsers] = useState([]);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const recurrenceEnabled = Form.useWatch("recurrenceEnabled", form);
  const recurrenceType = Form.useWatch("recurrenceType", form);
  const defaultHostName = currentUser?.displayName || "";
  const attendeeOptions = useMemo(
    () => [
      {
        value: "@all",
        label: t("@all（所有有效用户）"),
        selectedLabel: "@all",
        allUsers: true,
      },
      ...groups.map((group) => ({
        value: group.selectionValue || `@group:${group.id}`,
        label: t("{{name}}（用户组）", { name: group.name }),
        selectedLabel: group.name,
        groupId: group.id,
        groupName: group.name,
        memberCount: group.memberCount,
      })),
      ...directoryUsers.map((user) => ({
        value: user.email,
        label: `${user.displayName} · ${user.email} · ${displayJobTitle(user)}`,
        selectedLabel: user.displayName,
        displayName: user.displayName,
        username: user.username,
        email: user.email,
        jobTitle: user.jobTitle,
      })),
    ],
    [directoryUsers, groups, language],
  );
  const attendeeSelectedLabels = useMemo(
    () =>
      new Map(
        attendeeOptions.map((option) => [String(option.value), option.selectedLabel || option.label]),
      ),
    [attendeeOptions],
  );
  const legacyAttendees = useMemo(
    () => new Set((initialValues?.attendees || []).map((attendee) => String(attendee))),
    [initialValues],
  );

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    setAttendeeQuery("");

    if (mode === "edit" && initialValues) {
      form.setFieldsValue({
        ...toFormValues(initialValues),
        updateScope: "single",
        recurrenceEnabled: false,
        recurrenceType: "weekly",
        recurrenceInterval: 1,
        recurrenceCount: 12,
      });
      return;
    }

    const start = dayjs().minute(0).second(0).millisecond(0).add(1, "hour");
    form.setFieldsValue({
      title: "",
      hostName: defaultHostName,
      attendees: currentUser?.email ? [currentUser.email] : [],
      startTime: start,
      endTime: start.add(1, "hour"),
      maxOccupants: 30,
      passwordRequired: false,
      recurrenceEnabled: false,
      recurrenceType: "weekly",
      recurrenceInterval: 1,
      recurrenceCount: 12,
    });
  }, [currentUser?.email, defaultHostName, form, initialValues, mode, open]);

  useEffect(() => {
    if (!open) {
      setDirectoryUsers([]);
      setDirectoryLoading(false);
      return undefined;
    }

    let active = true;
    const timer = window.setTimeout(
      async () => {
        setDirectoryLoading(true);
        try {
          const data = await searchUserDirectory(attendeeQuery);
          if (active) {
            setDirectoryUsers(data.items || []);
          }
        } catch {
          if (active) {
            setDirectoryUsers([]);
          }
        } finally {
          if (active) {
            setDirectoryLoading(false);
          }
        }
      },
      attendeeQuery.trim() ? 250 : 0,
    );

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [attendeeQuery, open]);

  return (
    <Modal
      title={mode === "edit" ? t("编辑会议") : t("创建会议")}
      open={open}
      onCancel={onCancel}
      footer={null}
      centered
      width={620}
      destroyOnHidden
      className="meeting-modal"
    >
      <Form
        layout="vertical"
        form={form}
        onFinish={(values) => {
          const start = values.startTime;
          const end = values.endTime;
          if (start && end && !end.isAfter(start)) {
            message.error(t("结束时间必须晚于开始时间"));
            return;
          }
          onSubmit(toPayload(values), values.updateScope || "single");
        }}
      >
        <Form.Item
          label={t("会议标题")}
          name="title"
          rules={[{ required: true, message: t("请输入会议标题") }]}
        >
          <Input placeholder={t("例如：项目例会")} maxLength={80} />
        </Form.Item>

        <Form.Item
          label={t("主持人")}
          name="hostName"
          rules={[{ required: true, message: t("请输入主持人") }]}
        >
          <Input placeholder={t("例如：William Li")} maxLength={60} />
        </Form.Item>

        <Form.Item
          label={t("参会者名单")}
          name="attendees"
          extra={t("可选择 @all、整个用户组、具体用户，或输入外部邮箱")}
        >
          <Select
            mode="tags"
            showSearch
            filterOption={false}
            options={attendeeOptions}
            onSearch={setAttendeeQuery}
            onChange={(values, selectedOptions = []) => {
              const validValues = values.filter(
                (value, index) =>
                  selectedOptions[index]?.email ||
                  selectedOptions[index]?.allUsers ||
                  selectedOptions[index]?.groupId ||
                  normalizeIdentity(value) === "@all" ||
                  normalizeIdentity(value).startsWith("@group:") ||
                  isEmailLike(value) ||
                  legacyAttendees.has(String(value)),
              );
              if (validValues.length !== values.length) {
                form.setFieldValue("attendees", validValues);
                message.warning(t("请选择 @all、用户组、补全列表中的具体用户，或输入完整邮箱"));
              }
            }}
            loading={directoryLoading}
            notFoundContent={directoryLoading ? <Spin size="small" /> : t("未找到匹配用户，可直接输入邮箱")}
            tagRender={({ label, value, closable, onClose }) => (
              <Tag
                closable={closable}
                onClose={onClose}
                onMouseDown={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                }}
              >
                {attendeeSelectedLabels.get(String(value)) || label}
              </Tag>
            )}
            optionRender={(option) =>
              option.data.allUsers ? (
                <div className="attendee-option">
                  <span className="attendee-option-name">@all</span>
                  <span className="attendee-option-meta">{t("邀请所有有效用户")}</span>
                </div>
              ) : option.data.groupId ? (
                <div className="attendee-option">
                  <span className="attendee-option-name">{option.data.groupName}</span>
                  <span className="attendee-option-meta">{t("用户组 ·")}{option.data.memberCount}{t("名成员")}</span>
                </div>
              ) : option.data.email ? (
                <div className="attendee-option">
                  <span className="attendee-option-name">{option.data.displayName}</span>
                  <span className="attendee-option-meta">
                    {option.data.email} · {displayJobTitle(option.data)}
                  </span>
                </div>
              ) : (
                <div className="attendee-option">
                  <span className="attendee-option-name">{option.label}</span>
                  <span className="attendee-option-meta">
                    {isEmailLike(option.value) ? t("按回车添加外部邮箱") : t("请从匹配结果中选择具体用户")}
                  </span>
                </div>
              )
            }
            tokenSeparators={[",", ";", "\n"]}
            placeholder={t("选择用户组，或搜索姓名、用户名、邮箱、职称")}
            suffixIcon={<Users size={16} />}
            maxTagCount="responsive"
          />
        </Form.Item>

        <Form.Item
          label={t("开始时间")}
          name="startTime"
          rules={[{ required: true, message: t("请选择会议时间") }]}
        >
          <DatePicker
            showTime
            format="YYYY-MM-DD HH:mm"
            placement="topLeft"
            className="full-width"
            onChange={(value) => {
              if (mode === "create") {
                form.setFieldValue("endTime", value ? value.add(1, "hour") : null);
              }
            }}
          />
        </Form.Item>
        <Form.Item
          label={t("结束时间")}
          name="endTime"
          rules={[{ required: true, message: t("请选择会议结束时间") }]}
        >
          <DatePicker
            showTime
            format="YYYY-MM-DD HH:mm"
            placement="topLeft"
            className="full-width"
          />
        </Form.Item>

        {mode === "create" && (
          <div className="recurrence-section">
            <Form.Item label={t("周期性会议")} name="recurrenceEnabled" valuePropName="checked">
              <Switch checkedChildren={t("是")} unCheckedChildren={t("否")} />
            </Form.Item>

            {recurrenceEnabled && (
              <div className="recurrence-controls">
                <Form.Item
                  label={t("重复频率")}
                  name="recurrenceType"
                  rules={[{ required: true, message: t("请选择重复频率") }]}
                >
                  <Select
                    options={recurrenceTypeOptions.map((option) => ({
                      ...option,
                      label: t(option.label),
                    }))}
                  />
                </Form.Item>

                {recurrenceType === "every_n_days" && (
                  <Form.Item
                    label={t("每隔天数")}
                    name="recurrenceInterval"
                    rules={[{ required: true, message: t("请输入间隔天数") }]}
                  >
                    <InputNumber min={1} max={365} className="full-width" addonAfter={t("天")} />
                  </Form.Item>
                )}

                <Form.Item
                  label={t("生成场次")}
                  name="recurrenceCount"
                  rules={[{ required: true, message: t("请输入生成场次") }]}
                >
                  <InputNumber min={2} max={1000} className="full-width" addonAfter={t("场")} />
                </Form.Item>
              </div>
            )}
          </div>
        )}

        {mode === "edit" && initialValues?.isRecurring && (
          <Form.Item
            label={t("修改范围")}
            name="updateScope"
            extra={t("本次及后续会从当前场次开始修改；已经结束或取消的场次不会改变。")}
          >
            <Select
              options={[
                { value: "single", label: t("仅修改本次会议") },
                { value: "following", label: t("修改本次及后续会议") },
                { value: "series", label: t("修改整个系列") },
              ]}
            />
          </Form.Item>
        )}

        <div className="form-grid">
          <Form.Item
            label={t("最大人数")}
            name="maxOccupants"
            rules={[{ required: true, message: t("请输入最大人数") }]}
          >
            <InputNumber min={1} max={500} className="full-width" />
          </Form.Item>

          <Form.Item label={t("创建密码")} name="passwordRequired" valuePropName="checked">
            <Switch checkedChildren={t("是")} unCheckedChildren={t("否")} />
          </Form.Item>
        </div>

        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>{t("取消")}</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<Plus size={16} />}>
            {mode === "edit" ? t("保存") : t("创建")}
          </Button>
        </Flex>
      </Form>
    </Modal>
  );
}

function CreationResultModal({ meeting, onClose, onCopy }) {
  const protectedMeeting = Boolean(meeting?.passwordRequired);
  const rawLinkValue = protectedMeeting
    ? meeting?.accessUrl || meeting?.meetingUrl
    : meeting?.jitsiUrl || meeting?.meetingUrl;
  const linkValue = absoluteUrl(rawLinkValue);
  const shareText = meeting ? buildMeetingShareText(meeting, linkValue) : "";

  return (
    <Modal
      title={meeting?.password ? t("会议访问方式") : t("会议已创建")}
      open={Boolean(meeting)}
      onCancel={onClose}
      footer={[
        <Button key="close" type="primary" onClick={onClose}>{t("完成")}</Button>,
      ]}
      centered
      width={560}
    >
      {meeting && (
        <div className="result-box">
          {meeting.createdCount && (
            <div className="series-result">
              <Repeat2 size={18} />
              <Text>{t("已创建")}{meeting.createdCount}{t("场周期会议，首场链接如下。")}</Text>
            </div>
          )}

          <div>
            <Text type="secondary">{protectedMeeting ? t("入会验证链接") : t("会议链接")}</Text>
            <div className="copy-line">
              <Text className="copy-value">{linkValue}</Text>
              <Button
                icon={<Clipboard size={16} />}
                onClick={() => onCopy(linkValue)}
                aria-label={t("复制会议链接")}
              />
            </div>
          </div>

          {protectedMeeting && meeting.password && (
            <div>
              <Text type="secondary">{t("一次性会议密码")}</Text>
              <div className="copy-line password-line">
                <Text className="copy-value">{meeting.password}</Text>
                <Button
                  icon={<Clipboard size={16} />}
                  onClick={() => onCopy(meeting.password)}
                  aria-label={t("复制会议密码")}
                />
              </div>
            </div>
          )}

          <Button
            block
            className="share-copy-button"
            icon={<Clipboard size={16} />}
            onClick={() => onCopy(shareText)}
          >{t("一键复制分享信息")}</Button>
        </div>
      )}
    </Modal>
  );
}

function CalendarSubscriptionModal({ open, subscription, loading, onClose, onCopy }) {
  return (
    <Modal
      title={t("订阅个人会议日历")}
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="done" type="primary" onClick={onClose}>
          {t("完成")}
        </Button>,
      ]}
      centered
      width={600}
    >
      <Spin spinning={loading}>
        <div className="calendar-subscription-content">
          <Text>{t("订阅后，日历应用会自动同步您作为参会者的会议及后续变更。")}</Text>
          {subscription && (
            <>
              <div className="subscription-url-box">
                <Text type="secondary">{t("个人订阅地址")}</Text>
                <div className="copy-line">
                  <Text code className="copy-value">
                    {subscription.subscriptionUrl}
                  </Text>
                  <Button
                    icon={<Clipboard size={16} />}
                    onClick={() => onCopy(subscription.subscriptionUrl)}
                    aria-label={t("复制订阅地址")}
                  />
                </div>
              </div>
              <Flex gap={8} wrap>
                <Button
                  icon={<Clipboard size={16} />}
                  onClick={() => onCopy(subscription.subscriptionUrl)}
                >
                  {t("复制订阅地址")}
                </Button>
                <Button
                  type="primary"
                  icon={<ExternalLink size={16} />}
                  href={subscription.webcalUrl}
                >
                  {t("打开日历应用")}
                </Button>
              </Flex>
              <Text type="secondary" className="subscription-help">
                {t("也可以在 Google 日历、Outlook 或 Apple 日历中选择“通过网址订阅”，然后粘贴此地址。")}
              </Text>
              <Text type="warning" className="subscription-warning">
                {t("此地址可查看您的个人会议安排，请勿公开分享。")}
              </Text>
            </>
          )}
        </div>
      </Spin>
    </Modal>
  );
}

function getJoinRoomId() {
  const match = window.location.pathname.match(/^\/join\/([^/?#]+)/);
  if (!match) return null;

  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

function JoinPage({ roomId }) {
  const [form] = Form.useForm();
  const [meeting, setMeeting] = useState(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  useEffect(() => {
    let active = true;

    async function loadMeeting() {
      setLoading(true);
      try {
        const data = await fetchPublicMeeting(roomId);
        if (active) {
          setMeeting(data);
          setLoadError("");
        }
      } catch (error) {
        if (active) {
          setLoadError(error.message);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadMeeting();
    return () => {
      active = false;
    };
  }, [roomId]);

  const handleJoin = async (values) => {
    setVerifying(true);
    try {
      const data = await verifyMeetingPassword(roomId, values.password);
      window.location.assign(data.jitsiUrl);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="join-shell">
      {contextHolder}
      <div className="join-panel">
        <div className="join-icon">
          <LockKeyhole size={28} />
        </div>

        {loading ? (
          <Spin />
        ) : loadError ? (
          <Space direction="vertical" size={12} className="join-content">
            <Title level={2}>{t("无法打开会议")}</Title>
            <Text type="secondary">{loadError}</Text>
          </Space>
        ) : meeting ? (
          <Space direction="vertical" size={18} className="join-content">
            <div>
              <Text type="secondary">{meeting.roomId}</Text>
              <Title level={2}>{meeting.title}</Title>
            </div>

            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label={t("主持人")}>{meeting.hostName}</Descriptions.Item>
              <Descriptions.Item label={t("开始时间")}>{displayTime(meeting.startTime)}</Descriptions.Item>
              <Descriptions.Item label={t("结束时间")}>{displayTime(meeting.endTime)}</Descriptions.Item>
            </Descriptions>

            {meeting.passwordRequired ? (
              <Form form={form} layout="vertical" onFinish={handleJoin} className="join-form">
                <Form.Item
                  label={t("会议密码")}
                  name="password"
                  rules={[{ required: true, message: t("请输入会议密码") }]}
                >
                  <Input.Password
                    autoFocus
                    inputMode="numeric"
                    maxLength={4}
                    placeholder={t("请输入4位数字密码")}
                    onInput={(event) => {
                      event.currentTarget.value = event.currentTarget.value.replace(/\D/g, "").slice(0, 4);
                    }}
                  />
                </Form.Item>
                <Button
                  type="primary"
                  size="large"
                  htmlType="submit"
                  block
                  loading={verifying}
                  icon={<LockKeyhole size={18} />}
                >{t("验证并进入")}</Button>
              </Form>
            ) : (
              <Button
                type="primary"
                size="large"
                block
                href={meeting.jitsiUrl}
                target="_blank"
                rel="noreferrer"
                icon={<ExternalLink size={18} />}
              >{t("打开会议")}</Button>
            )}
          </Space>
        ) : null}
      </div>
    </div>
  );
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [resetMethod, setResetMethod] = useState("email");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const handleLogin = async (values) => {
    setSubmitting(true);
    try {
      const data = await login(values);
      onAuthenticated(data.user);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRegister = async (values) => {
    setSubmitting(true);
    try {
      await registerAccount(values);
      messageApi.success(t("申请已提交，请等待管理员审核"));
      setMode("login");
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handlePasswordResetRequest = async (values) => {
    setSubmitting(true);
    try {
      await requestPasswordReset(values);
      messageApi.success(t("找回密码申请已提交，请等待管理员处理"));
      setMode("login");
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleEmailPasswordReset = async (values) => {
    setSubmitting(true);
    try {
      const data = await requestEmailPasswordReset(values);
      messageApi.success(data.message);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-shell">
      {contextHolder}
      <div className="auth-panel">
        <div className="brand-block auth-brand">
          <div className="brand-icon">
            <Video size={24} />
          </div>
          <div>
            <Title level={3}>{t("会议管理系统")}</Title>
            <Text type="secondary">{t("登录后管理会议预约")}</Text>
          </div>
        </div>

        <Segmented
          block
          value={mode}
          onChange={setMode}
          options={[
            { label: t("登录"), value: "login" },
            { label: t("申请账号"), value: "register" },
            { label: t("找回密码"), value: "reset" },
          ]}
        />

        {mode === "login" ? (
          <Form layout="vertical" onFinish={handleLogin} className="auth-form">
            <Form.Item
              label={t("用户名或邮箱")}
              name="account"
              rules={[{ required: true, message: t("请输入用户名或邮箱") }]}
            >
              <Input autoFocus autoComplete="username" maxLength={120} />
            </Form.Item>
            <Form.Item
              label={t("密码")}
              name="password"
              rules={[{ required: true, message: t("请输入密码") }]}
            >
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={submitting}
              icon={<ShieldCheck size={18} />}
            >{t("登录")}</Button>
          </Form>
        ) : mode === "register" ? (
          <Form layout="vertical" onFinish={handleRegister} className="auth-form">
            <Form.Item
              label={t("用户名")}
              name="username"
              rules={[
                { required: true, message: t("请输入用户名") },
                {
                  pattern: /^[A-Za-z0-9_.-]{3,40}$/,
                  message: t("用户名需为 3-40 位字母、数字、点、横线或下划线"),
                },
              ]}
            >
              <Input autoFocus autoComplete="username" maxLength={40} />
            </Form.Item>
            <Form.Item
              label={t("用户昵称（真实姓名）")}
              name="displayName"
              rules={[{ required: true, message: t("请输入用户昵称（真实姓名）") }]}
            >
              <Input maxLength={80} />
            </Form.Item>
            <Form.Item
              label={t("职称")}
              name="jobTitle"
              rules={[{ required: true, message: t("请输入职称") }]}
            >
              <Input maxLength={80} placeholder={t("例如：教授、项目经理、工程师")} />
            </Form.Item>
            <Form.Item
              label={t("邮箱")}
              name="email"
              rules={[
                { required: true, message: t("请输入邮箱") },
                { type: "email", message: t("邮箱格式无效") },
              ]}
            >
              <Input autoComplete="email" maxLength={120} />
            </Form.Item>
            <Form.Item
              label={t("密码")}
              name="password"
              rules={[
                { required: true, message: t("请输入密码") },
                { min: 8, message: t("密码至少需要 8 位") },
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label={t("给管理员的留言")}
              name="registerMessage"
              rules={[{ required: true, message: t("请填写给管理员的留言") }]}
            >
              <Input.TextArea rows={3} maxLength={240} showCount />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={submitting}
              icon={<UserPlus size={18} />}
            >{t("提交申请")}</Button>
          </Form>
        ) : (
          <div className="auth-form">
            <Segmented
              block
              value={resetMethod}
              onChange={setResetMethod}
              options={[
                { label: t("邮件找回"), value: "email", icon: <Mail size={15} /> },
                { label: t("管理员协助"), value: "admin", icon: <LockKeyhole size={15} /> },
              ]}
            />

            {resetMethod === "email" ? (
              <Form
                key="email-reset"
                layout="vertical"
                onFinish={handleEmailPasswordReset}
                className="reset-form"
              >
                <Text type="secondary">{t("输入账号绑定的邮箱，我们会发送一个限时、一次性使用的重置链接。")}</Text>
                <Form.Item
                  label={t("绑定邮箱")}
                  name="email"
                  rules={[
                    { required: true, message: t("请输入绑定邮箱") },
                    { type: "email", message: t("邮箱格式无效") },
                  ]}
                >
                  <Input autoFocus autoComplete="email" maxLength={120} prefix={<Mail size={16} />} />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  size="large"
                  block
                  loading={submitting}
                  icon={<Mail size={18} />}
                >{t("发送重置邮件")}</Button>
              </Form>
            ) : (
              <Form
                key="admin-reset"
                layout="vertical"
                onFinish={handlePasswordResetRequest}
                className="reset-form"
              >
                <Text type="secondary">{t("无法接收邮件时，可以提交申请并等待管理员处理。")}</Text>
                <Form.Item
                  label={t("用户名或邮箱")}
                  name="account"
                  rules={[{ required: true, message: t("请输入用户名或邮箱") }]}
                >
                  <Input autoFocus autoComplete="username" maxLength={120} />
                </Form.Item>
                <Form.Item
                  label={t("给管理员的说明")}
                  name="message"
                  rules={[{ required: true, message: t("请填写找回密码说明") }]}
                >
                  <Input.TextArea rows={3} maxLength={240} showCount />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  size="large"
                  block
                  loading={submitting}
                  icon={<LockKeyhole size={18} />}
                >{t("提交找回申请")}</Button>
              </Form>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EmailPasswordResetPage({ token }) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const handleSubmit = async (values) => {
    setSubmitting(true);
    try {
      await resetPasswordWithToken({ token, ...values });
      const cleanUrl = `${window.location.pathname}${window.location.hash}`;
      window.history.replaceState({}, "", cleanUrl || "/");
      setCompleted(true);
      form.resetFields();
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-shell">
      {contextHolder}
      <div className="auth-panel">
        <div className="brand-block auth-brand">
          <div className="brand-icon">
            <LockKeyhole size={24} />
          </div>
          <div>
            <Title level={3}>{completed ? t("密码已更新") : t("设置新密码")}</Title>
            <Text type="secondary">
              {completed ? t("现在可以使用新密码登录") : t("为账号设置一个新的登录密码")}
            </Text>
          </div>
        </div>

        {completed ? (
          <Button type="primary" size="large" block onClick={() => window.location.assign("/")}>{t("返回登录")}</Button>
        ) : (
          <Form form={form} layout="vertical" onFinish={handleSubmit} className="auth-form">
            <Form.Item
              label={t("新密码")}
              name="newPassword"
              rules={[
                { required: true, message: t("请输入新密码") },
                { min: 8, message: t("密码至少需要 8 位") },
              ]}
            >
              <Input.Password autoFocus autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label={t("确认新密码")}
              name="confirmPassword"
              dependencies={["newPassword"]}
              rules={[
                { required: true, message: t("请再次输入新密码") },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("newPassword") === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error(t("两次输入的新密码不一致")));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={submitting}
              icon={<LockKeyhole size={18} />}
            >{t("确认修改")}</Button>
          </Form>
        )}
      </div>
    </div>
  );
}

function RefreshButton({ onRefresh, ariaLabel = t("刷新") }) {
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    const animationFinished = new Promise((resolve) => window.setTimeout(resolve, 650));
    try {
      await Promise.resolve().then(onRefresh);
    } finally {
      await animationFinished;
      setRefreshing(false);
    }
  };

  return (
    <Button
      type="text"
      icon={<RefreshCw size={16} className={refreshing ? "refresh-icon is-spinning" : "refresh-icon"} />}
      onClick={handleRefresh}
      disabled={refreshing}
      aria-label={ariaLabel}
      aria-busy={refreshing}
    />
  );
}

function AdminReviewSection({ pendingUsers, loadingId, onApprove, onReject, onRefresh }) {
  return (
    <section className="side-section review-section">
      <Flex align="center" justify="space-between">
        <Space size={8}>
          <Text strong>{t("账号审核")}</Text>
          <Badge count={pendingUsers.length} size="small" />
        </Space>
        <RefreshButton onRefresh={onRefresh} ariaLabel={t("刷新账号审核")} />
      </Flex>
      <List
        size="small"
        dataSource={pendingUsers}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无待审核账号")} /> }}
        renderItem={(item) => (
          <List.Item>
            <div className="review-line">
              <div className="review-copy">
                <Text strong>{item.displayName}</Text>
                <Text type="secondary">@{item.username}</Text>
                <Text type="secondary">{displayJobTitle(item)}</Text>
                <Text className="review-message">{item.registerMessage}</Text>
              </div>
              <Space size={6} wrap>
                <Button
                  size="small"
                  type="primary"
                  icon={<UserCheck size={14} />}
                  loading={loadingId === item.id}
                  onClick={() => onApprove(item)}
                >{t("通过")}</Button>
                <Popconfirm
                  title={t("拒绝账号申请")}
                  description={t("被拒绝的账号不能登录系统。")}
                  okText={t("拒绝")}
                  cancelText={t("取消")}
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onReject(item)}
                >
                  <Button size="small" danger icon={<XCircle size={14} />} loading={loadingId === item.id}>{t("拒绝")}</Button>
                </Popconfirm>
              </Space>
            </div>
          </List.Item>
        )}
      />
    </section>
  );
}

function PasswordResetSection({ requests, loadingId, onApprove, onReject, onRefresh }) {
  return (
    <section className="side-section review-section">
      <Flex align="center" justify="space-between">
        <Space size={8}>
          <Text strong>{t("密码找回")}</Text>
          <Badge count={requests.length} size="small" />
        </Space>
        <RefreshButton onRefresh={onRefresh} ariaLabel={t("刷新密码找回")} />
      </Flex>
      <List
        size="small"
        dataSource={requests}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无找回申请")} /> }}
        renderItem={(item) => (
          <List.Item>
            <div className="review-line">
              <div className="review-copy">
                <Text strong>{item.displayName}</Text>
                <Text type="secondary">@{item.username}</Text>
                <Text className="review-message">{item.message}</Text>
              </div>
              <Space size={6} wrap>
                <Button
                  size="small"
                  type="primary"
                  icon={<LockKeyhole size={14} />}
                  loading={loadingId === item.id}
                  onClick={() => onApprove(item)}
                >{t("重置")}</Button>
                <Popconfirm
                  title={t("拒绝找回申请")}
                  description={t("此申请会被标记为已拒绝。")}
                  okText={t("拒绝")}
                  cancelText={t("取消")}
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onReject(item)}
                >
                  <Button size="small" danger icon={<XCircle size={14} />} loading={loadingId === item.id}>{t("拒绝")}</Button>
                </Popconfirm>
              </Space>
            </div>
          </List.Item>
        )}
      />
    </section>
  );
}

function UserFormModal({ open, mode, initialValues, loading, onCancel, onSubmit }) {
  const [form] = Form.useForm();

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    if (mode === "edit" && initialValues) {
      form.setFieldsValue({
        displayName: initialValues.displayName,
        email: initialValues.email,
        jobTitle: initialValues.jobTitle,
        role: initialValues.role,
        status: initialValues.status,
      });
      return;
    }
    form.setFieldsValue({
      role: "scheduler",
      status: "active",
    });
  }, [form, initialValues, mode, open]);

  return (
    <Modal
      title={mode === "edit" ? t("编辑用户") : t("新建用户")}
      open={open}
      onCancel={onCancel}
      footer={null}
      centered
      width={520}
      destroyOnHidden
    >
      <Form layout="vertical" form={form} onFinish={onSubmit}>
        {mode === "create" && (
          <Form.Item
            label={t("用户名")}
            name="username"
            rules={[
              { required: true, message: t("请输入用户名") },
              {
                pattern: /^[A-Za-z0-9_.-]{3,40}$/,
                message: t("用户名需为 3-40 位字母、数字、点、横线或下划线"),
              },
            ]}
          >
            <Input maxLength={40} />
          </Form.Item>
        )}

        <Form.Item
          label={t("用户昵称（真实姓名）")}
          name="displayName"
          rules={[{ required: true, message: t("请输入用户昵称（真实姓名）") }]}
        >
          <Input maxLength={80} />
        </Form.Item>

        <Form.Item
          label={t("邮箱")}
          name="email"
          rules={[
            { required: true, message: t("请输入邮箱") },
            { type: "email", message: t("邮箱格式无效") },
          ]}
        >
          <Input maxLength={120} />
        </Form.Item>

        <Form.Item
          label={t("职称")}
          name="jobTitle"
          rules={[{ required: true, message: t("请输入职称") }]}
        >
          <Input maxLength={80} placeholder={t("例如：教授、项目经理、工程师")} />
        </Form.Item>

        {mode === "create" && (
          <Form.Item
            label={t("初始密码")}
            name="password"
            rules={[
              { required: true, message: t("请输入初始密码") },
              { min: 8, message: t("密码至少需要 8 位") },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        )}

        <div className="form-grid">
          <Form.Item label={t("角色")} name="role" rules={[{ required: true, message: t("请选择角色") }]}>
            <Select
              options={[
                { label: t("员工"), value: "scheduler" },
                { label: t("管理员"), value: "admin" },
              ]}
            />
          </Form.Item>

          <Form.Item label={t("状态")} name="status" rules={[{ required: true, message: t("请选择状态") }]}>
            <Select
              options={[
                { label: t("正常"), value: "active" },
                { label: t("待审核"), value: "pending" },
                { label: t("已拒绝"), value: "rejected" },
                { label: t("已停用"), value: "disabled" },
              ]}
            />
          </Form.Item>
        </div>

        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>{t("取消")}</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<UserCheck size={16} />}>{t("保存")}</Button>
        </Flex>
      </Form>
    </Modal>
  );
}

function TemporaryPasswordModal({ result, onClose, onCopy }) {
  const temporaryPassword = result?.temporaryPassword;
  return (
    <Modal
      title={t("临时密码")}
      open={Boolean(result)}
      onCancel={onClose}
      footer={[
        <Button key="close" type="primary" onClick={onClose}>{t("完成")}</Button>,
      ]}
      centered
      width={520}
    >
      {result && (
        <div className="result-box">
          <Text type="secondary">{t("请把临时密码发给")}{result.user?.displayName || result.request?.displayName}{t("，对方登录后应尽快在右上角再次修改。")}</Text>
          <div className="copy-line password-line">
            <Text className="copy-value">{temporaryPassword}</Text>
            <Button
              icon={<Clipboard size={16} />}
              onClick={() => onCopy(temporaryPassword)}
              aria-label={t("复制临时密码")}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}

function UserManagementDrawer({
  open,
  users,
  currentUser,
  loadingId,
  onClose,
  onCreate,
  onEdit,
  onDelete,
  onResetPassword,
}) {
  return (
    <Drawer
      title={t("用户管理")}
      open={open}
      onClose={onClose}
      width={640}
      extra={
        <Button type="primary" icon={<UserPlus size={16} />} onClick={onCreate}>{t("新建用户")}</Button>
      }
    >
      <List
        dataSource={users}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无用户")} /> }}
        renderItem={(item) => (
          <List.Item>
            <div className="user-row">
              <div className="user-row-main">
                <Space wrap size={8}>
                  <Text strong>{item.displayName}</Text>
                  <Text type="secondary">@{item.username}</Text>
                  {item.id === currentUser.id && <Tag color="cyan">{t("当前账号")}</Tag>}
                </Space>
                <Text type="secondary">{item.email || t("未填写邮箱")}</Text>
                <Text type="secondary">{t("职称：")}{displayJobTitle(item)}</Text>
                <Space wrap size={6}>
                  <Tag color={item.role === "admin" ? "gold" : "blue"}>
                    {roleLabels[item.role] ? t(roleLabels[item.role]) : item.role}
                  </Tag>
                  <Tag color={userStatusColors[item.status] || "default"}>
                    {userStatusLabels[item.status] ? t(userStatusLabels[item.status]) : item.status}
                  </Tag>
                </Space>
              </div>
              <Space size={6} wrap className="user-row-actions">
                <Button size="small" icon={<Edit3 size={14} />} onClick={() => onEdit(item)}>{t("编辑")}</Button>
                <Popconfirm
                  title={t("重置用户密码")}
                  description={t("系统会生成临时密码并让旧会话失效。")}
                  okText={t("重置")}
                  cancelText={t("取消")}
                  onConfirm={() => onResetPassword(item)}
                >
                  <Button size="small" icon={<LockKeyhole size={14} />} loading={loadingId === item.id}>{t("重置密码")}</Button>
                </Popconfirm>
                <Popconfirm
                  title={t("删除用户")}
                  description={t("删除后该用户不能再登录系统。")}
                  okText={t("删除")}
                  cancelText={t("取消")}
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onDelete(item)}
                >
                  <Button size="small" danger icon={<Trash2 size={14} />} loading={loadingId === item.id}>{t("删除")}</Button>
                </Popconfirm>
              </Space>
            </div>
          </List.Item>
        )}
      />
    </Drawer>
  );
}

function GroupFormModal({ open, mode, initialValues, loading, onCancel, onSubmit }) {
  const [form] = Form.useForm();

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue({
      name: mode === "edit" ? initialValues?.name : "",
      description: mode === "edit" ? initialValues?.description : "",
    });
  }, [form, initialValues, mode, open]);

  return (
    <Modal
      title={mode === "edit" ? t("编辑用户组") : t("创建用户组")}
      open={open}
      onCancel={onCancel}
      footer={null}
      centered
      width={520}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={onSubmit}>
        <Form.Item
          label={t("用户组名称")}
          name="name"
          rules={[{ required: true, message: t("请输入用户组名称") }]}
        >
          <Input autoFocus maxLength={80} placeholder={t("例如：产品研发组")} />
        </Form.Item>
        <Form.Item label={t("用户组说明")} name="description">
          <Input.TextArea rows={4} maxLength={500} showCount placeholder={t("可填写用途或范围")} />
        </Form.Item>
        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>{t("取消")}</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<Users size={16} />}>{t("保存")}</Button>
        </Flex>
      </Form>
    </Modal>
  );
}

function GroupMemberModal({ open, group, loading, onCancel, onSubmit }) {
  const [form] = Form.useForm();
  const [query, setQuery] = useState("");
  const [directoryUsers, setDirectoryUsers] = useState([]);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const existingMemberIds = useMemo(
    () => new Set((group?.members || []).map((member) => member.id)),
    [group],
  );
  const userOptions = useMemo(
    () =>
      directoryUsers
        .filter((user) => !existingMemberIds.has(user.id))
        .map((user) => ({
          value: user.id,
          label: `${user.displayName} · ${user.email} · ${displayJobTitle(user)}`,
          ...user,
        })),
    [directoryUsers, existingMemberIds],
  );

  useEffect(() => {
    if (!open) return;
    form.resetFields();
    form.setFieldsValue({ groupRole: "member" });
    setQuery("");
  }, [form, group?.id, open]);

  useEffect(() => {
    if (!open) {
      setDirectoryUsers([]);
      setDirectoryLoading(false);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(
      async () => {
        setDirectoryLoading(true);
        try {
          const data = await searchUserDirectory(query);
          if (active) setDirectoryUsers(data.items || []);
        } catch {
          if (active) setDirectoryUsers([]);
        } finally {
          if (active) setDirectoryLoading(false);
        }
      },
      query.trim() ? 250 : 0,
    );
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  return (
    <Modal
      title={group ? t("添加成员 · {{name}}", { name: group.name }) : t("添加组成员")}
      open={open}
      onCancel={onCancel}
      footer={null}
      centered
      width={520}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" onFinish={onSubmit}>
        <Form.Item
          label={t("用户（可多选）")}
          name="userIds"
          rules={[{ required: true, message: t("请至少选择一名用户") }]}
        >
          <Select
            mode="multiple"
            labelInValue
            allowClear
            showSearch
            filterOption={false}
            options={userOptions}
            onSearch={setQuery}
            loading={directoryLoading}
            notFoundContent={directoryLoading ? <Spin size="small" /> : t("没有可添加的匹配用户")}
            placeholder={t("搜索并选择一个或多个用户")}
            maxTagCount="responsive"
            optionRender={(option) => (
              <div className="attendee-option">
                <span className="attendee-option-name">{option.data.displayName}</span>
                <span className="attendee-option-meta">
                  {option.data.email} · {displayJobTitle(option.data)}
                </span>
              </div>
            )}
          />
        </Form.Item>
        {group?.canManageGroup && (
          <Form.Item label={t("组内角色")} name="groupRole" rules={[{ required: true }]}>
            <Select
              options={[
                { label: t("普通成员"), value: "member" },
                { label: t("组管理员"), value: "admin" },
              ]}
            />
          </Form.Item>
        )}
        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>{t("取消")}</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<UserPlus size={16} />}>{t("添加成员")}</Button>
        </Flex>
      </Form>
    </Modal>
  );
}

function GroupManagementDrawer({
  open,
  groups,
  loading,
  isSystemAdmin,
  onClose,
  onCreate,
  onEdit,
  onDelete,
  onAddMember,
  onRemoveMember,
  onChangeMemberRole,
  onRefresh,
}) {
  const collapseItems = groups.map((group) => {
    const adminCount = group.members.filter((member) => member.groupRole === "admin").length;
    return {
      key: String(group.id),
      label: (
        <div className="group-collapse-label">
          <div>
            <Text strong>{group.name}</Text>
            <Text type="secondary">{group.memberCount}{t("名成员")}</Text>
          </div>
          <Space size={6} wrap>
            {group.currentUserGroupRole === "admin" && <Tag color="gold">{t("组管理员")}</Tag>}
            {isSystemAdmin && (
              <Tag color={group.includesCurrentUser ? "cyan" : "default"}>
                {group.includesCurrentUser ? t("包含我") : t("不包含我")}
              </Tag>
            )}
          </Space>
        </div>
      ),
      children: (
        <div className="group-detail">
          <Text type="secondary">{group.description || t("暂无用户组说明")}</Text>
          <Space wrap className="group-actions">
            {group.canAddMembers && (
              <Button size="small" type="primary" icon={<UserPlus size={14} />} onClick={() => onAddMember(group)}>{t("添加成员")}</Button>
            )}
            {group.canManageGroup && (
              <Button size="small" icon={<Edit3 size={14} />} onClick={() => onEdit(group)}>{t("编辑组")}</Button>
            )}
            {group.canDeleteGroup && (
              <Popconfirm
                title={t("删除用户组")}
                description={t("只会删除用户组，不会删除组内用户。")}
                okText={t("删除")}
                cancelText={t("取消")}
                okButtonProps={{ danger: true }}
                onConfirm={() => onDelete(group)}
              >
                <Button size="small" danger icon={<Trash2 size={14} />}>{t("删除组")}</Button>
              </Popconfirm>
            )}
          </Space>
          <List
            size="small"
            dataSource={group.members}
            locale={{ emptyText: t("暂无成员") }}
            renderItem={(member) => {
              const isLastAdmin = member.groupRole === "admin" && adminCount <= 1;
              return (
                <List.Item>
                  <div className="group-member-row">
                    <div className="group-member-copy">
                      <Space size={6} wrap>
                        <Text strong>{member.displayName}</Text>
                        <Text type="secondary">@{member.username}</Text>
                        {member.groupRole === "admin" && <Tag color="gold">{t("管理员")}</Tag>}
                        {member.status !== "active" && (
                          <Tag color={userStatusColors[member.status] || "default"}>
                            {userStatusLabels[member.status]
                              ? t(userStatusLabels[member.status])
                              : member.status}
                          </Tag>
                        )}
                      </Space>
                      <Text type="secondary">
                        {member.email} · {displayJobTitle(member)}
                      </Text>
                    </div>
                    {group.canManageGroup && (
                      <Space size={6} wrap>
                        <Button
                          size="small"
                          disabled={isLastAdmin}
                          onClick={() =>
                            onChangeMemberRole(
                              group,
                              member,
                              member.groupRole === "admin" ? "member" : "admin",
                            )
                          }
                        >
                          {member.groupRole === "admin" ? t("设为成员") : t("设为管理员")}
                        </Button>
                        <Popconfirm
                          title={t("移除组成员")}
                          description={t("该用户会从本组移除，但账号不会被删除。")}
                          okText={t("移除")}
                          cancelText={t("取消")}
                          okButtonProps={{ danger: true }}
                          disabled={isLastAdmin}
                          onConfirm={() => onRemoveMember(group, member)}
                        >
                          <Button size="small" danger disabled={isLastAdmin}>{t("移除")}</Button>
                        </Popconfirm>
                      </Space>
                    )}
                  </div>
                </List.Item>
              );
            }}
          />
        </div>
      ),
    };
  });

  return (
    <Drawer
      title={isSystemAdmin ? t("全部用户组") : t("我的用户组")}
      open={open}
      onClose={onClose}
      width={720}
      extra={
        <Space>
          <RefreshButton onRefresh={onRefresh} ariaLabel={t("刷新用户组")} />
          <Button type="primary" icon={<Plus size={16} />} onClick={onCreate}>{t("创建组")}</Button>
        </Space>
      }
    >
      <Spin spinning={loading}>
        {collapseItems.length ? (
          <Collapse items={collapseItems} className="group-collapse" />
        ) : (
          <Empty description={t("暂无用户组，可以先创建一个")} />
        )}
      </Spin>
    </Drawer>
  );
}

function PreferencesPopover({
  preferences,
  onChange,
  customThemeColor,
  customThemeSaving,
  onCustomThemeSave,
  onCustomThemeClear,
  emailNotificationsEnabled,
  emailPreferenceSaving,
  onEmailNotificationsChange,
  onOpenPasswordChange,
}) {
  const [open, setOpen] = useState(false);
  const [customColorInput, setCustomColorInput] = useState(customThemeColor || "");
  const normalizedCustomColor = customColorInput.trim().toLowerCase();
  const customColorValid = /^#[0-9a-f]{6}$/.test(normalizedCustomColor);

  useEffect(() => {
    setCustomColorInput(customThemeColor || "");
  }, [customThemeColor]);

  const content = (
    <div className="preferences-card">
      <div className="preferences-section">
        <Text strong>
          <Space size={6}>
            <Languages size={15} />
            {t("语言")}
          </Space>
        </Text>
        <Segmented
          block
          value={preferences.language}
          onChange={(value) => onChange({ ...preferences, language: value })}
          options={[
            { label: "中文", value: "zh-CN" },
            { label: "EN", value: "en-US" },
          ]}
          aria-label={t("语言")}
        />
      </div>

      <div className="preferences-section">
        <Text strong>{t("显示模式")}</Text>
        <Segmented
          block
          value={preferences.appearanceMode}
          onChange={(value) => onChange({ ...preferences, appearanceMode: value })}
          options={[
            {
              value: "light",
              label: (
                <Space size={6}>
                  <Sun size={15} />{t("浅色")}</Space>
              ),
            },
            {
              value: "dark",
              label: (
                <Space size={6}>
                  <Moon size={15} />{t("深色")}</Space>
              ),
            },
            {
              value: "system",
              label: (
                <Space size={6}>
                  <Monitor size={15} />{t("跟随系统")}</Space>
              ),
            },
          ]}
        />
      </div>

      <div className="preferences-section">
        <Text strong>{t("主题颜色")}</Text>
        <div className="preference-color-grid">
          {colorThemeOptions.map((option) => {
            const selected = preferences.colorTheme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                className={`preference-color-option ${selected ? "is-selected" : ""}`}
                style={{ "--swatch-color": option.color }}
                aria-label={t("使用{{color}}主题", { color: t(option.label) })}
                aria-pressed={selected}
                onClick={() => onChange({ ...preferences, colorTheme: option.value })}
              >
                <span className="preference-color-swatch">
                  {selected && <Check size={14} />}
                </span>
                <span>{t(option.label)}</span>
              </button>
            );
          })}
          <button
            type="button"
            className={`preference-color-option ${preferences.colorTheme === "custom" ? "is-selected" : ""}`}
            style={{ "--swatch-color": customThemeColor || "#94a3b8" }}
            aria-label={t("使用自定义主题")}
            aria-pressed={preferences.colorTheme === "custom"}
            disabled={!customThemeColor}
            onClick={() => onChange({ ...preferences, colorTheme: "custom" })}
          >
            <span className="preference-color-swatch">
              {preferences.colorTheme === "custom" && <Check size={14} />}
            </span>
            <span>{t("自定义")}</span>
          </button>
        </div>
        <div className="custom-color-editor">
          <Input
            value={customColorInput}
            maxLength={7}
            placeholder="#1f6feb"
            status={customColorInput && !customColorValid ? "error" : undefined}
            onChange={(event) => setCustomColorInput(event.target.value)}
            aria-label={t("自定义主题颜色")}
            prefix={
              <span
                className="custom-color-preview"
                style={{ background: customColorValid ? normalizedCustomColor : "#94a3b8" }}
              />
            }
          />
          <Space size={6}>
            <Button
              type="primary"
              loading={customThemeSaving}
              disabled={!customColorValid}
              onClick={() => onCustomThemeSave(normalizedCustomColor)}
            >{t("保存并使用")}</Button>
            {customThemeColor && (
              <Button loading={customThemeSaving} onClick={onCustomThemeClear}>{t("清除")}</Button>
            )}
          </Space>
        </div>
        <Text type="secondary" className="custom-color-help">{t("输入 # 加 6 位十六进制字符，例如 #1f6feb")}</Text>
      </div>

      <div className="preferences-section">
        <Text strong>{t("账号设置")}</Text>
        <div className="preference-toggle-row">
          <div className="preference-toggle-copy">
            <Text>{t("会议邮件提醒")}</Text>
            <Text type="secondary">{t("被添加为参会者时接收邀请邮件")}</Text>
          </div>
          <Switch
            checked={emailNotificationsEnabled}
            loading={emailPreferenceSaving}
            onChange={onEmailNotificationsChange}
            aria-label={t("接收会议邮件提醒")}
          />
        </div>
        <Button
          block
          icon={<LockKeyhole size={16} />}
          onClick={() => {
            setOpen(false);
            onOpenPasswordChange();
          }}
        >{t("修改密码")}</Button>
      </div>
    </div>
  );

  return (
    <Popover
      title={t("首选项")}
      content={content}
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
    >
      <Button icon={<Palette size={16} />}>{t("首选项")}</Button>
    </Popover>
  );
}

function ManagementApp({
  currentUser,
  onCurrentUserChange,
  onAccountCustomColorChange,
  onLogout,
  preferences,
  onPreferenceChange,
}) {
  const [meetings, setMeetings] = useState([]);
  const [groups, setGroups] = useState([]);
  const [users, setUsers] = useState([]);
  const [pendingUsers, setPendingUsers] = useState([]);
  const [passwordResetRequests, setPasswordResetRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reviewingId, setReviewingId] = useState(null);
  const [passwordResetReviewingId, setPasswordResetReviewingId] = useState(null);
  const [userActionId, setUserActionId] = useState(null);
  const [userSaving, setUserSaving] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [passwordForm] = Form.useForm();
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [passwordChanging, setPasswordChanging] = useState(false);
  const [emailPreferenceSaving, setEmailPreferenceSaving] = useState(false);
  const [customThemeSaving, setCustomThemeSaving] = useState(false);
  const [groupDrawerOpen, setGroupDrawerOpen] = useState(false);
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [groupFormMode, setGroupFormMode] = useState("create");
  const [groupMemberOpen, setGroupMemberOpen] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [groupSaving, setGroupSaving] = useState(false);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [userDrawerOpen, setUserDrawerOpen] = useState(false);
  const [userFormOpen, setUserFormOpen] = useState(false);
  const [userFormMode, setUserFormMode] = useState("create");
  const [selectedUser, setSelectedUser] = useState(null);
  const [temporaryPasswordResult, setTemporaryPasswordResult] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState("create");
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [createdMeeting, setCreatedMeeting] = useState(null);
  const [calendarSubscriptionOpen, setCalendarSubscriptionOpen] = useState(false);
  const [calendarSubscription, setCalendarSubscription] = useState(null);
  const [calendarSubscriptionLoading, setCalendarSubscriptionLoading] = useState(false);
  const [calendarValue, setCalendarValue] = useState(dayjs());
  const [calendarView, setCalendarView] = useState("week");
  const [messageApi, contextHolder] = message.useMessage();
  const isAdmin = currentUser?.role === "admin";

  const handleEmailNotificationsChange = async (enabled) => {
    setEmailPreferenceSaving(true);
    try {
      const data = await updateCurrentUserPreferences({ emailNotificationsEnabled: enabled });
      onCurrentUserChange(data.user);
      messageApi.success(enabled ? t("已开启会议邮件提醒") : t("已关闭会议邮件提醒"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setEmailPreferenceSaving(false);
    }
  };

  const handleCustomThemeSave = async (customThemeColor) => {
    setCustomThemeSaving(true);
    try {
      const data = await updateCurrentUserPreferences({ customThemeColor });
      onCurrentUserChange(data.user);
      onAccountCustomColorChange(data.user.customThemeColor);
      onPreferenceChange({ ...preferences, colorTheme: "custom" });
      messageApi.success(t("自定义颜色已保存并应用"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setCustomThemeSaving(false);
    }
  };

  const handleCustomThemeClear = async () => {
    setCustomThemeSaving(true);
    try {
      const data = await updateCurrentUserPreferences({ customThemeColor: null });
      onCurrentUserChange(data.user);
      onAccountCustomColorChange(null);
      if (preferences.colorTheme === "custom") {
        onPreferenceChange({ ...preferences, colorTheme: defaultPreferences.colorTheme });
      }
      messageApi.success(t("自定义颜色已清除"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setCustomThemeSaving(false);
    }
  };

  const loadData = async ({ silent = false } = {}) => {
    if (!silent) {
      setLoading(true);
    }
    try {
      const requests = [fetchMeetings(), fetchGroups()];
      if (isAdmin) {
        requests.push(fetchUsers(), fetchPasswordResetRequests("pending"));
      }
      const [meetingData, groupData, userData, resetData] = await Promise.all(requests);
      setMeetings(meetingData.items || []);
      setGroups(groupData.items || []);
      const userItems = isAdmin ? userData?.items || [] : [];
      setUsers(userItems);
      setPendingUsers(userItems.filter((item) => item.status === "pending"));
      setPasswordResetRequests(isAdmin ? resetData?.items || [] : []);
    } catch (error) {
      if (error.status === 401) {
        onLogout();
        return;
      }
      messageApi.error(error.message);
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    loadData();
    const timer = window.setInterval(() => {
      loadData({ silent: true });
    }, 30000);
    return () => window.clearInterval(timer);
  }, [currentUser?.id]);

  const refreshGroups = async () => {
    setGroupsLoading(true);
    try {
      const data = await fetchGroups();
      setGroups(data.items || []);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupsLoading(false);
    }
  };

  const replaceGroup = (updatedGroup) => {
    setGroups((items) => {
      if (!isAdmin && !updatedGroup.includesCurrentUser) {
        return items.filter((group) => group.id !== updatedGroup.id);
      }
      return items.map((group) => (group.id === updatedGroup.id ? updatedGroup : group));
    });
  };

  const openCreateGroup = () => {
    setGroupFormMode("create");
    setSelectedGroup(null);
    setGroupFormOpen(true);
  };

  const openEditGroup = (group) => {
    setGroupFormMode("edit");
    setSelectedGroup(group);
    setGroupFormOpen(true);
  };

  const openAddGroupMember = (group) => {
    setSelectedGroup(group);
    setGroupMemberOpen(true);
  };

  const handleGroupSubmit = async (values) => {
    setGroupSaving(true);
    try {
      if (groupFormMode === "edit" && selectedGroup) {
        const data = await updateGroup(selectedGroup.id, values);
        replaceGroup(data.group);
        messageApi.success(t("用户组已更新"));
      } else {
        const data = await createGroup(values);
        setGroups((items) => [...items, data.group].sort((a, b) => a.name.localeCompare(b.name)));
        messageApi.success(t("用户组已创建"));
      }
      setGroupFormOpen(false);
      setSelectedGroup(null);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupSaving(false);
    }
  };

  const handleDeleteGroup = async (group) => {
    setGroupSaving(true);
    try {
      await deleteGroup(group.id);
      setGroups((items) => items.filter((item) => item.id !== group.id));
      if (selectedGroup?.id === group.id) setSelectedGroup(null);
      messageApi.success(t("用户组已删除"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupSaving(false);
    }
  };

  const handleAddGroupMember = async (values) => {
    if (!selectedGroup) return;
    const userIds = (values.userIds || []).map((item) =>
      typeof item === "object" ? item.value : item,
    );
    if (!userIds.length) {
      messageApi.error(t("请至少选择一名用户"));
      return;
    }
    setGroupSaving(true);
    try {
      const data = await addGroupMember(selectedGroup.id, {
        userIds,
        groupRole: selectedGroup.canManageGroup ? values.groupRole : "member",
      });
      replaceGroup(data.group);
      setSelectedGroup(data.group);
      setGroupMemberOpen(false);
      messageApi.success(t("已添加 {{count}} 名成员", { count: userIds.length }));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupSaving(false);
    }
  };

  const handleRemoveGroupMember = async (group, member) => {
    setGroupSaving(true);
    try {
      const data = await removeGroupMember(group.id, member.id);
      replaceGroup(data.group);
      messageApi.success(t("成员已移除"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupSaving(false);
    }
  };

  const handleChangeGroupMemberRole = async (group, member, groupRole) => {
    setGroupSaving(true);
    try {
      const data = await updateGroupMember(group.id, member.id, { groupRole });
      replaceGroup(data.group);
      messageApi.success(groupRole === "admin" ? t("已设为组管理员") : t("已设为普通成员"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setGroupSaving(false);
    }
  };

  const handleApproveUser = async (user) => {
    setReviewingId(user.id);
    try {
      const data = await approveUser(user.id, "scheduler");
      setUsers((items) => items.map((item) => (item.id === user.id ? data.user : item)));
      setPendingUsers((items) => items.filter((item) => item.id !== user.id));
      messageApi.success(t("账号已通过审核"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setReviewingId(null);
    }
  };

  const handleRejectUser = async (user) => {
    setReviewingId(user.id);
    try {
      const data = await rejectUser(user.id);
      setUsers((items) => items.map((item) => (item.id === user.id ? data.user : item)));
      setPendingUsers((items) => items.filter((item) => item.id !== user.id));
      messageApi.success(t("账号申请已拒绝"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setReviewingId(null);
    }
  };

  const handleApprovePasswordReset = async (item) => {
    setPasswordResetReviewingId(item.id);
    try {
      const data = await approvePasswordResetRequest(item.id);
      setPasswordResetRequests((items) => items.filter((requestItem) => requestItem.id !== item.id));
      setUsers((items) => items.map((user) => (user.id === data.user.id ? data.user : user)));
      setTemporaryPasswordResult(data);
      messageApi.success(t("密码已重置"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setPasswordResetReviewingId(null);
    }
  };

  const handleRejectPasswordReset = async (item) => {
    setPasswordResetReviewingId(item.id);
    try {
      await rejectPasswordResetRequest(item.id);
      setPasswordResetRequests((items) => items.filter((requestItem) => requestItem.id !== item.id));
      messageApi.success(t("找回申请已拒绝"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setPasswordResetReviewingId(null);
    }
  };

  const openCreateUser = () => {
    setUserFormMode("create");
    setSelectedUser(null);
    setUserFormOpen(true);
  };

  const openEditUser = (user) => {
    setUserFormMode("edit");
    setSelectedUser(user);
    setUserFormOpen(true);
  };

  const handleUserSubmit = async (values) => {
    setUserSaving(true);
    try {
      if (userFormMode === "edit" && selectedUser) {
        const data = await updateUser(selectedUser.id, values);
        if (data.user.id === currentUser.id) {
          onCurrentUserChange(data.user);
        }
        setUsers((items) => items.map((item) => (item.id === selectedUser.id ? data.user : item)));
        setPendingUsers((items) =>
          data.user.status === "pending"
            ? items.map((item) => (item.id === data.user.id ? data.user : item))
            : items.filter((item) => item.id !== data.user.id),
        );
        messageApi.success(t("用户已更新"));
      } else {
        const data = await createUser(values);
        setUsers((items) => [data.user, ...items]);
        if (data.user.status === "pending") {
          setPendingUsers((items) => [data.user, ...items]);
        }
        messageApi.success(t("用户已创建"));
      }
      setUserFormOpen(false);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setUserSaving(false);
    }
  };

  const handleDeleteUser = async (user) => {
    setUserActionId(user.id);
    try {
      await deleteUser(user.id);
      setUsers((items) => items.filter((item) => item.id !== user.id));
      setPendingUsers((items) => items.filter((item) => item.id !== user.id));
      messageApi.success(t("用户已删除"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setUserActionId(null);
    }
  };

  const handleResetUserPassword = async (user) => {
    setUserActionId(user.id);
    try {
      const data = await resetUserPassword(user.id);
      setUsers((items) => items.map((item) => (item.id === user.id ? data.user : item)));
      setTemporaryPasswordResult(data);
      messageApi.success(t("密码已重置"));
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setUserActionId(null);
    }
  };
  const handleChangePassword = async (values) => {
  setPasswordChanging(true);

  try {
    await changePassword(values);
    messageApi.success(t("密码已修改，请重新登录"));
    setPasswordModalOpen(false);
    passwordForm.resetFields();
    onLogout();
  } catch (error) {
    messageApi.error(error.message);
  } finally {
    setPasswordChanging(false);
  }
};

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      // Local auth state is still cleared if the server-side session is already gone.
    } finally {
      setLoggingOut(false);
      onLogout();
    }
  };

  const visibleMeetings = useMemo(
    () => meetings.filter((meeting) => isMeetingAttendee(meeting, currentUser)),
    [currentUser, meetings],
  );

  const meetingsByDate = useMemo(() => {
    const grouped = new Map();
    visibleMeetings.forEach((meeting) => {
      const key = dayjs(meeting.startTime).format("YYYY-MM-DD");
      const list = grouped.get(key) || [];
      list.push(meeting);
      grouped.set(
        key,
        list.sort((a, b) => dayjs(a.startTime).valueOf() - dayjs(b.startTime).valueOf()),
      );
    });
    return grouped;
  }, [visibleMeetings]);

  const relatedMeetings = useMemo(
    () =>
      [...visibleMeetings]
        .sort((a, b) => {
          const aStart = dayjs(a.startTime).valueOf();
          const bStart = dayjs(b.startTime).valueOf();
          const safeAStart = Number.isFinite(aStart) ? aStart : Number.MAX_SAFE_INTEGER;
          const safeBStart = Number.isFinite(bStart) ? bStart : Number.MAX_SAFE_INTEGER;
          return safeAStart - safeBStart || a.id - b.id;
        }),
    [visibleMeetings],
  );

  const selectedMeetingFresh = useMemo(() => {
    if (!selectedMeeting) return null;
    return visibleMeetings.find((meeting) => meeting.id === selectedMeeting.id) || null;
  }, [selectedMeeting, visibleMeetings]);

  const summary = useMemo(
    () => ({
      total: visibleMeetings.length,
      running: visibleMeetings.filter((meeting) => meeting.status === "Running").length,
      scheduled: visibleMeetings.filter((meeting) => meeting.status === "Scheduled").length,
      finished: visibleMeetings.filter((meeting) => meeting.status === "Finished").length,
    }),
    [visibleMeetings],
  );

  const calendarDates = useMemo(
    () => {
      const localizedValue = calendarValue.locale(
        preferences.language === "en-US" ? "en" : "zh-cn",
      );
      return calendarView === "week"
        ? buildWeekDates(localizedValue)
        : buildMonthDates(localizedValue);
    },
    [calendarValue, calendarView, preferences.language],
  );

  const calendarRangeLabel = useMemo(
    () => formatCalendarRange(calendarValue, calendarView, preferences.language),
    [calendarValue, calendarView, preferences.language],
  );

  const openCreate = () => {
    setFormMode("create");
    setSelectedMeeting(null);
    setFormOpen(true);
  };

  const openEdit = (meeting) => {
    setFormMode("edit");
    setSelectedMeeting(meeting);
    setFormOpen(true);
  };

  const handleSubmit = async (payload, scope = "single") => {
    setSaving(true);
    try {
      if (formMode === "edit" && selectedMeetingFresh) {
        const updated = await updateMeeting(selectedMeetingFresh.id, payload, scope);
        const affectedMeetings = (updated.affectedMeetings?.length
          ? updated.affectedMeetings
          : [updated]
        ).map((meeting) => (meeting.id === updated.id ? { ...meeting, ...updated } : meeting));
        const affectedById = new Map(affectedMeetings.map((meeting) => [meeting.id, meeting]));
        setMeetings((items) =>
          items.map((item) => affectedById.get(item.id) || item),
        );
        setSelectedMeeting(updated);
        if (updated.password) {
          setCreatedMeeting(updated);
        }
        showMeetingSaveMessage(messageApi, t("会议已更新"), updated.emailNotification);
      } else {
        const created = await createMeeting(payload);
        const createdItems = created.seriesMeetings?.length
          ? [created, ...created.seriesMeetings.filter((meeting) => meeting.id !== created.id)]
          : [created];
        setMeetings((items) => [...createdItems, ...items]);
        setCreatedMeeting(created);
        showMeetingSaveMessage(
          messageApi,
          created.createdCount
            ? t("已创建 {{count}} 场周期会议", { count: created.createdCount })
            : t("会议已创建"),
          created.emailNotification,
        );
      }
      setFormOpen(false);
    } catch (error) {
      messageApi.error(error.message);
    } finally {
      setSaving(false);
    }
  };

  const handleCancelMeeting = async (meeting, scope = "single") => {
    try {
      const updated = await deleteMeeting(meeting.id, scope);
      const affectedById = new Map(
        (updated.affectedMeetings || []).map((affectedMeeting) => [
          affectedMeeting.id,
          affectedMeeting,
        ]),
      );
      setMeetings((items) => items.map((item) => affectedById.get(item.id) || item));
      setSelectedMeeting(null);
      const successText = updated.cancelledCount > 1
        ? t("已取消 {{count}} 场会议", { count: updated.cancelledCount })
        : t("会议已取消");
      showMeetingSaveMessage(messageApi, successText, updated.emailNotification);
    } catch (error) {
      messageApi.error(error.message);
    }
  };

  const openCalendarSubscription = async () => {
    setCalendarSubscriptionOpen(true);
    if (calendarSubscription) return;
    setCalendarSubscriptionLoading(true);
    try {
      setCalendarSubscription(await createCalendarSubscription());
    } catch (error) {
      setCalendarSubscriptionOpen(false);
      messageApi.error(error.message);
    } finally {
      setCalendarSubscriptionLoading(false);
    }
  };

  const copyText = async (text) => {
    try {
      await writeClipboardText(text);
      messageApi.success(t("已复制"));
    } catch {
      messageApi.error(t("复制失败"));
    }
  };

  const goToday = () => setCalendarValue(dayjs());
  const goPrevious = () => setCalendarValue((value) => value.subtract(1, calendarView));
  const goNext = () => setCalendarValue((value) => value.add(1, calendarView));
  const selectedMeetingRawLink = selectedMeetingFresh?.passwordRequired
    ? selectedMeetingFresh.accessUrl || selectedMeetingFresh.meetingUrl
    : selectedMeetingFresh?.jitsiUrl || selectedMeetingFresh?.meetingUrl;
  const selectedMeetingLink = absoluteUrl(selectedMeetingRawLink);
  const selectedMeetingLinkLabel = selectedMeetingFresh?.passwordRequired ? t("入会验证链接") : t("会议链接");

  const renderCalendarCell = (current) => {
    const key = current.format("YYYY-MM-DD");
    const dayMeetings = meetingsByDate.get(key) || [];
    const isOutsideMonth = calendarView === "month" && !current.isSame(calendarValue, "month");
    const isSelected = current.isSame(calendarValue, "day");
    const isToday = current.isSame(dayjs(), "day");

    return (
      <div
        key={key}
        role="button"
        tabIndex={0}
        className={[
          "calendar-cell",
          isOutsideMonth ? "is-outside-month" : "",
          isSelected ? "is-selected-day" : "",
          isToday ? "is-today" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        onClick={() => setCalendarValue(current)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setCalendarValue(current);
          }
        }}
      >
        <div className="calendar-day">
          <span>{current.format(t("M月D日"))}</span>
        </div>
        <div className="meeting-stack">
          {dayMeetings.map((meeting) => {
            const status = statusMap[meeting.status] || statusMap.Scheduled;
            return (
              <button
                key={meeting.id}
                type="button"
                className={`meeting-chip ${status.className}`}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedMeeting(meeting);
                }}
              >
                <span className="chip-time">{dayjs(meeting.startTime).format("HH:mm")}</span>
                <span className="chip-title">
                  {meeting.isRecurring && <Repeat2 size={12} className="chip-repeat-icon" />}
                  <span>{meeting.title}</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="app-shell">
      {contextHolder}
      <aside className="side-panel">
        <div className="brand-block">
          <div className="brand-icon">
            <Video size={24} />
          </div>
          <div>
            <Title level={3}>{t("会议管理系统")}</Title>
            <Text type="secondary">Wusupower meeting console</Text>
          </div>
        </div>

        <Button
          type="primary"
          size="large"
          block
          icon={<Plus size={18} />}
          onClick={openCreate}
          className="create-button"
        >{t("创建会议")}</Button>

        <div className="summary-grid">
          <div>
            <strong>{summary.total}</strong>
            <span>{t("全部")}</span>
          </div>
          <div>
            <strong>{summary.scheduled}</strong>
            <span>{t("预约")}</span>
          </div>
          <div>
            <strong>{summary.running}</strong>
            <span>{t("进行")}</span>
          </div>
          <div>
            <strong>{summary.finished}</strong>
            <span>{t("结束")}</span>
          </div>
        </div>

        {isAdmin && (
          <>
            <Button
              block
              icon={<Users size={18} />}
              onClick={() => setUserDrawerOpen(true)}
              className="user-management-button"
            >{t("用户管理")}</Button>

            <AdminReviewSection
              pendingUsers={pendingUsers}
              loadingId={reviewingId}
              onApprove={handleApproveUser}
              onReject={handleRejectUser}
              onRefresh={() => loadData({ silent: true })}
            />

            <PasswordResetSection
              requests={passwordResetRequests}
              loadingId={passwordResetReviewingId}
              onApprove={handleApprovePasswordReset}
              onReject={handleRejectPasswordReset}
              onRefresh={() => loadData({ silent: true })}
            />
          </>
        )}

        <section className="side-section">
          <Flex align="center" justify="space-between">
            <Text strong>{t("我的参会会议")}</Text>
            <RefreshButton onRefresh={loadData} />
          </Flex>
          <List
            size="small"
            dataSource={relatedMeetings}
            locale={{
              emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无参会会议")} />,
            }}
            renderItem={(meeting) => (
              <List.Item
                className="related-meeting-item"
                role="button"
                tabIndex={0}
                onClick={() => setSelectedMeeting(meeting)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedMeeting(meeting);
                  }
                }}
              >
                <div className="related-meeting-line">
                  <Badge status={statusMap[meeting.status]?.color || "default"} />
                  <div>
                    <Text>{meeting.title}</Text>
                    <Text type="secondary">{displayTime(meeting.startTime)}</Text>
                  </div>
                </div>
              </List.Item>
            )}
          />
        </section>
      </aside>

      <main className="calendar-panel">
        <Flex align="center" justify="space-between" className="topbar">
          <div>
            <Title level={2}>{t("会议日历")}</Title>
            <Text type="secondary">{t("点击会议实体查看链接、参会者和编辑入口")}</Text>
          </div>
          <Space size={12} wrap className="user-actions">
            <div className="user-pill">
              <Text strong>{currentUser.displayName}</Text>
              <Text type="secondary">{displayJobTitle(currentUser, true)}</Text>
            </div>
            <PreferencesPopover
              preferences={preferences}
              onChange={onPreferenceChange}
              customThemeColor={currentUser.customThemeColor}
              customThemeSaving={customThemeSaving}
              onCustomThemeSave={handleCustomThemeSave}
              onCustomThemeClear={handleCustomThemeClear}
              emailNotificationsEnabled={currentUser.emailNotificationsEnabled !== false}
              emailPreferenceSaving={emailPreferenceSaving}
              onEmailNotificationsChange={handleEmailNotificationsChange}
              onOpenPasswordChange={() => setPasswordModalOpen(true)}
            />
            <Button icon={<CalendarPlus size={16} />} onClick={openCalendarSubscription}>
              {t("订阅日历")}
            </Button>
            <Button icon={<Users size={16} />} onClick={() => setGroupDrawerOpen(true)}>{t("用户组")}</Button>
            <Button icon={<LogOut size={16} />} loading={loggingOut} onClick={handleLogout}>{t("退出")}</Button>
          </Space>
        </Flex>

        <Spin spinning={loading}>
          <section className="meeting-calendar">
            <div className="calendar-toolbar">
              <Space size={8}>
                <Button icon={<ChevronLeft size={16} />} onClick={goPrevious} aria-label={t("上一页")} />
                <Button onClick={goToday}>{t("今天")}</Button>
                <Button icon={<ChevronRight size={16} />} onClick={goNext} aria-label={t("下一页")} />
              </Space>
              <Text strong className="calendar-range">
                {calendarRangeLabel}
              </Text>
              <Segmented
                value={calendarView}
                onChange={(value) => setCalendarView(value)}
                options={[
                  { label: t("周"), value: "week" },
                  { label: t("月"), value: "month" },
                ]}
              />
            </div>

            <div className={`calendar-grid ${calendarView === "week" ? "is-week-view" : "is-month-view"}`}>
              {weekLabels.map((label) => (
                <div key={label} className="calendar-weekday">
                  {t(label)}
                </div>
              ))}
              {calendarDates.map(renderCalendarCell)}
            </div>
          </section>
        </Spin>
      </main>

      <Drawer
        title={t("会议信息")}
        open={Boolean(selectedMeetingFresh)}
        onClose={() => setSelectedMeeting(null)}
        width={520}
        extra={
          selectedMeetingFresh && !["Finished", "Cancelled"].includes(selectedMeetingFresh.status) && (
            <Space wrap>
              <Button icon={<Edit3 size={16} />} onClick={() => openEdit(selectedMeetingFresh)}>{t("编辑")}</Button>
              <Popconfirm
                title={selectedMeetingFresh.isRecurring ? t("取消本次会议") : t("取消会议")}
                description={t("参与人员会收到取消通知，会议记录会保留为已取消。")}
                okText={t("确认取消")}
                cancelText={t("取消")}
                okButtonProps={{ danger: true }}
                onConfirm={() => handleCancelMeeting(selectedMeetingFresh)}
              >
                <Button danger icon={<XCircle size={16} />}>
                  {selectedMeetingFresh.isRecurring ? t("取消本次") : t("取消会议")}
                </Button>
              </Popconfirm>
              {selectedMeetingFresh.isRecurring && (
                <>
                  <Popconfirm
                    title={t("取消本次及后续会议")}
                    description={t("当前场次和后续尚未结束的场次都会取消。")}
                    okText={t("确认取消")}
                    cancelText={t("返回")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() => handleCancelMeeting(selectedMeetingFresh, "following")}
                  >
                    <Button danger icon={<Repeat2 size={16} />}>{t("取消本次及以后")}</Button>
                  </Popconfirm>
                  <Popconfirm
                    title={t("取消整个系列")}
                    description={t("此系列中所有尚未结束的场次都会取消。")}
                    okText={t("取消整个系列")}
                    cancelText={t("返回")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() => handleCancelMeeting(selectedMeetingFresh, "series")}
                  >
                    <Button danger icon={<Trash2 size={16} />}>{t("取消整个系列")}</Button>
                  </Popconfirm>
                </>
              )}
            </Space>
          )
        }
      >
        {selectedMeetingFresh && (
          <Space direction="vertical" size={18} className="drawer-content">
            <div>
              <Space align="center" wrap>
                <Title level={3} className="drawer-title">
                  {selectedMeetingFresh.title}
                </Title>
                <Tag color={statusMap[selectedMeetingFresh.status]?.color}>
                  {t(statusMap[selectedMeetingFresh.status]?.text)}
                </Tag>
                {selectedMeetingFresh.isRecurring && (
                  <Tag icon={<Repeat2 size={12} />} color="cyan">{t("周期")}</Tag>
                )}
              </Space>
              <Text type="secondary">{selectedMeetingFresh.roomId}</Text>
            </div>

            <div className="link-box">
              <Link2 size={18} />
              <div className="link-copy-content">
                <Text type="secondary">{selectedMeetingLinkLabel}</Text>
                <Text className="link-text">{selectedMeetingLink}</Text>
              </div>
              <Button
                icon={<Clipboard size={16} />}
                onClick={() => copyText(selectedMeetingLink)}
                aria-label={t("复制会议链接")}
              />
            </div>

            <Descriptions column={1} size="middle" bordered>
              <Descriptions.Item label={t("创建者")}>
                {selectedMeetingFresh.hostName}
              </Descriptions.Item>
              <Descriptions.Item label={t("开始时间")}>
                {displayTime(selectedMeetingFresh.startTime)}
              </Descriptions.Item>
              <Descriptions.Item label={t("结束时间")}>
                {displayTime(selectedMeetingFresh.endTime)}
              </Descriptions.Item>
              <Descriptions.Item label={t("会议时长")}>
                {minutesBetween(selectedMeetingFresh.startTime, selectedMeetingFresh.endTime)}
              </Descriptions.Item>
              <Descriptions.Item label={t("周期规则")}>
                {recurrenceText(selectedMeetingFresh)}
              </Descriptions.Item>
              <Descriptions.Item label={t("最大人数")}>
                {selectedMeetingFresh.maxOccupants}
              </Descriptions.Item>
              <Descriptions.Item label={t("会议密码")}>
                {selectedMeetingFresh.passwordRequired ? t("已启用") : t("未启用")}
              </Descriptions.Item>
            </Descriptions>

            <section>
              <Flex align="center" gap={8} className="section-title">
                <Users size={18} />
                <Text strong>{t("参会者名单")}</Text>
              </Flex>
              {selectedMeetingFresh.attendees?.length ? (
                <div className="attendee-list">
                  {selectedMeetingFresh.attendees.map((attendee) => (
                    <Tag key={attendee}>{attendee}</Tag>
                  ))}
                </div>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无参会者")} />
              )}
            </section>

            <Button
              type="primary"
              block
              size="large"
              href={selectedMeetingLink}
              target="_blank"
              rel="noreferrer"
              icon={selectedMeetingFresh.passwordRequired ? <LockKeyhole size={18} /> : <CalendarClock size={18} />}
            >
              {selectedMeetingFresh.passwordRequired ? t("打开入会验证页") : t("打开会议")}
            </Button>
          </Space>
        )}
      </Drawer>

      <MeetingFormModal
        open={formOpen}
        mode={formMode}
        initialValues={formMode === "edit" ? selectedMeetingFresh : null}
        currentUser={currentUser}
        groups={groups}
        language={preferences.language}
        loading={saving}
        onCancel={() => setFormOpen(false)}
        onSubmit={handleSubmit}
      />

      <CreationResultModal
        meeting={createdMeeting}
        onClose={() => setCreatedMeeting(null)}
        onCopy={copyText}
      />

      <CalendarSubscriptionModal
        open={calendarSubscriptionOpen}
        subscription={calendarSubscription}
        loading={calendarSubscriptionLoading}
        onClose={() => setCalendarSubscriptionOpen(false)}
        onCopy={copyText}
      />

      <GroupManagementDrawer
        open={groupDrawerOpen}
        groups={groups}
        loading={groupsLoading || groupSaving}
        isSystemAdmin={isAdmin}
        onClose={() => setGroupDrawerOpen(false)}
        onCreate={openCreateGroup}
        onEdit={openEditGroup}
        onDelete={handleDeleteGroup}
        onAddMember={openAddGroupMember}
        onRemoveMember={handleRemoveGroupMember}
        onChangeMemberRole={handleChangeGroupMemberRole}
        onRefresh={refreshGroups}
      />

      <GroupFormModal
        open={groupFormOpen}
        mode={groupFormMode}
        initialValues={selectedGroup}
        loading={groupSaving}
        onCancel={() => setGroupFormOpen(false)}
        onSubmit={handleGroupSubmit}
      />

      <GroupMemberModal
        open={groupMemberOpen}
        group={selectedGroup}
        loading={groupSaving}
        onCancel={() => setGroupMemberOpen(false)}
        onSubmit={handleAddGroupMember}
      />

      <Modal
        title={t("修改密码")}
        open={passwordModalOpen}
        onCancel={() => {
          setPasswordModalOpen(false);
          passwordForm.resetFields();
        }}
        onOk={() => passwordForm.submit()}
        confirmLoading={passwordChanging}
        okText={t("确认修改")}
        cancelText={t("取消")}
        destroyOnHidden
      >
        <Form form={passwordForm} layout="vertical" onFinish={handleChangePassword}>
          <Form.Item
            label={t("当前密码")}
            name="currentPassword"
            rules={[{ required: true, message: t("请输入当前密码") }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>

          <Form.Item
            label={t("新密码")}
            name="newPassword"
            rules={[
              { required: true, message: t("请输入新密码") },
              { min: 8, message: t("密码至少需要 8 位") },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>

          <Form.Item
            label={t("确认新密码")}
            name="confirmPassword"
            dependencies={["newPassword"]}
            rules={[
              { required: true, message: t("请再次输入新密码") },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("newPassword") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error(t("两次输入的新密码不一致")));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>

      {isAdmin && (
        <>
          <UserManagementDrawer
            open={userDrawerOpen}
            users={users}
            currentUser={currentUser}
            loadingId={userActionId}
            onClose={() => setUserDrawerOpen(false)}
            onCreate={openCreateUser}
            onEdit={openEditUser}
            onDelete={handleDeleteUser}
            onResetPassword={handleResetUserPassword}
          />

          <UserFormModal
            open={userFormOpen}
            mode={userFormMode}
            initialValues={selectedUser}
            loading={userSaving}
            onCancel={() => setUserFormOpen(false)}
            onSubmit={handleUserSubmit}
          />

          <TemporaryPasswordModal
            result={temporaryPasswordResult}
            onClose={() => setTemporaryPasswordResult(null)}
            onCopy={copyText}
          />
        </>
      )}
    </div>
  );
}

function ManagementGate({ preferences, onPreferenceChange, onAccountCustomColorChange }) {
  const [currentUser, setCurrentUser] = useState(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadCurrentUser() {
      try {
        const data = await fetchCurrentUser();
        if (active) {
          setCurrentUser(data.user);
          onAccountCustomColorChange(data.user.customThemeColor || null);
        }
      } catch {
        if (active) {
          setCurrentUser(null);
        }
      } finally {
        if (active) {
          setChecking(false);
        }
      }
    }

    loadCurrentUser();
    return () => {
      active = false;
    };
  }, [onAccountCustomColorChange]);

  if (checking) {
    return (
      <div className="auth-shell">
        <Spin />
      </div>
    );
  }

  if (!currentUser) {
    return (
      <AuthScreen
        onAuthenticated={(user) => {
          setCurrentUser(user);
          onAccountCustomColorChange(user.customThemeColor || null);
        }}
      />
    );
  }

  return (
    <ManagementApp
      currentUser={currentUser}
      onCurrentUserChange={setCurrentUser}
      onAccountCustomColorChange={onAccountCustomColorChange}
      onLogout={() => {
        setCurrentUser(null);
        onAccountCustomColorChange(null);
      }}
      preferences={preferences}
      onPreferenceChange={onPreferenceChange}
    />
  );
}

function App() {
  const [preferences, setPreferences] = useState(loadPreferences);
  setLanguage(preferences.language);
  dayjs.locale(preferences.language === "en-US" ? "en" : "zh-cn");
  const [accountCustomColor, setAccountCustomColor] = useState(null);
  const [systemPrefersDark, setSystemPrefersDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches || false,
  );
  const darkModeEnabled =
    preferences.appearanceMode === "dark" ||
    (preferences.appearanceMode === "system" && systemPrefersDark);
  const selectedColorTheme = useMemo(
    () => {
      if (preferences.colorTheme === "custom" && /^#[0-9a-f]{6}$/i.test(accountCustomColor || "")) {
        return { value: "custom", label: "自定义", color: accountCustomColor };
      }
      return (
        colorThemeOptions.find((option) => option.value === preferences.colorTheme) ||
        colorThemeOptions[0]
      );
    },
    [accountCustomColor, preferences.colorTheme],
  );

  useEffect(() => {
    const colorSchemeQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!colorSchemeQuery) return undefined;
    const handleColorSchemeChange = (event) => setSystemPrefersDark(event.matches);
    setSystemPrefersDark(colorSchemeQuery.matches);
    if (typeof colorSchemeQuery.addEventListener === "function") {
      colorSchemeQuery.addEventListener("change", handleColorSchemeChange);
      return () => colorSchemeQuery.removeEventListener("change", handleColorSchemeChange);
    }
    colorSchemeQuery.addListener(handleColorSchemeChange);
    return () => colorSchemeQuery.removeListener(handleColorSchemeChange);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
    dayjs.locale(preferences.language === "en-US" ? "en" : "zh-cn");
    document.documentElement.lang = preferences.language === "en-US" ? "en" : "zh-CN";
    document.title = t("会议管理系统");
    document.documentElement.dataset.theme = darkModeEnabled ? "dark" : "light";
    document.documentElement.dataset.themeMode = preferences.appearanceMode;
    document.documentElement.dataset.colorTheme = preferences.colorTheme;
    if (selectedColorTheme.value === "custom") {
      document.documentElement.style.setProperty("--accent", selectedColorTheme.color);
      document.documentElement.style.setProperty(
        "--accent-strong",
        `color-mix(in srgb, ${selectedColorTheme.color} 80%, black)`,
      );
    } else {
      document.documentElement.style.removeProperty("--accent");
      document.documentElement.style.removeProperty("--accent-strong");
    }
  }, [darkModeEnabled, preferences, selectedColorTheme]);

  const joinRoomId = getJoinRoomId();
  let content;
  if (joinRoomId) {
    content = <JoinPage roomId={joinRoomId} />;
  } else {
    const resetToken = new URLSearchParams(window.location.search).get("resetToken");
    content = resetToken ? (
      <EmailPasswordResetPage token={resetToken} />
    ) : (
      <ManagementGate
        preferences={preferences}
        onPreferenceChange={setPreferences}
        onAccountCustomColorChange={setAccountCustomColor}
      />
    );
  }

  return (
    <ConfigProvider
      locale={preferences.language === "en-US" ? enUS : zhCN}
      theme={{
        algorithm: darkModeEnabled ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: selectedColorTheme.color,
          colorSuccess: "#2f855a",
          colorWarning: "#b7791f",
          colorError: "#c2413a",
          colorInfo: selectedColorTheme.color,
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: { controlHeight: 38, borderRadius: 8 },
          Modal: { borderRadiusLG: 8 },
          Drawer: { borderRadiusLG: 8 },
          Calendar: { borderRadiusLG: 8 },
        },
      }}
    >
      {content}
    </ConfigProvider>
  );
}

export default App;
