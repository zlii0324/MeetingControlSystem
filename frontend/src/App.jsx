import React, { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
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
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  DatePicker,
  message,
} from "antd";
import dayjs from "dayjs";
import {
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Edit3,
  ExternalLink,
  Link2,
  LogOut,
  LockKeyhole,
  Plus,
  RefreshCw,
  Repeat2,
  ShieldCheck,
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
  createMeeting,
  createUser,
  deleteMeeting,
  deleteUser,
  fetchAccessLogs,
  fetchCurrentUser,
  fetchMeetings,
  fetchPasswordResetRequests,
  fetchPublicMeeting,
  fetchUsers,
  login,
  logout,
  registerAccount,
  rejectPasswordResetRequest,
  rejectUser,
  requestPasswordReset,
  resetUserPassword,
  searchUserDirectory,
  updateUser,
  updateMeeting,
  verifyMeetingPassword,
  changePassword,
} from "./api";

const { Text, Title } = Typography;
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

function isEmailLike(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

function showMeetingSaveMessage(messageApi, successText, notification) {
  if (!notification?.requested) {
    messageApi.success(successText);
    return;
  }
  if (notification.status === "sent") {
    messageApi.success(`${successText}，已发送 ${notification.sent} 封通知邮件`);
    return;
  }
  if (notification.status === "partial") {
    messageApi.warning(
      `${successText}，已发送 ${notification.sent}/${notification.requested} 封通知邮件`,
    );
    return;
  }
  if (notification.status === "disabled") {
    messageApi.warning(`${successText}，但邮件通知尚未配置`);
    return;
  }
  messageApi.warning(`${successText}，但通知邮件发送失败`);
}

function minutesBetween(start, end) {
  const minutes = dayjs(end).diff(dayjs(start), "minute");
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function buildMeetingShareText(meeting, linkValue) {
  return [
    `会议：${meeting.title}`,
    `会议时间：${displayTime(meeting.startTime)} - ${displayTime(meeting.endTime)}`,
    `主持人：${meeting.hostName}`,
    meeting.createdCount ? `周期会议：已生成 ${meeting.createdCount} 场` : null,
    `会议链接：${linkValue}`,
    meeting.password ? `会议密码：${meeting.password}` : null,
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
  if (!recurrence) return "非周期会议";

  const count = recurrence.count ? `，共 ${recurrence.count} 场` : "";
  const current =
    Number.isInteger(recurrence.index) && recurrence.count
      ? `，当前第 ${recurrence.index + 1} 场`
      : "";

  if (recurrence.type === "weekly") return `每周重复${count}${current}`;
  if (recurrence.type === "biweekly") return `每两周重复${count}${current}`;
  if (recurrence.type === "every_n_days") {
    return `每 ${recurrence.interval || 1} 天重复${count}${current}`;
  }
  if (recurrence.type === "monthly") return `每月重复${count}${current}`;
  return `周期会议${count}${current}`;
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

function formatCalendarRange(value, view) {
  if (view === "month") {
    return value.format("YYYY年 M月");
  }

  const week = buildWeekDates(value);
  const start = week[0];
  const end = week[6];
  if (start.isSame(end, "month")) {
    return `${start.format("YYYY年 M月D日")} - ${end.format("D日")}`;
  }
  if (start.isSame(end, "year")) {
    return `${start.format("YYYY年 M月D日")} - ${end.format("M月D日")}`;
  }
  return `${start.format("YYYY年 M月D日")} - ${end.format("YYYY年 M月D日")}`;
}

function MeetingFormModal({ open, mode, initialValues, currentUser, onCancel, onSubmit, loading }) {
  const [form] = Form.useForm();
  const [attendeeQuery, setAttendeeQuery] = useState("");
  const [directoryUsers, setDirectoryUsers] = useState([]);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const recurrenceEnabled = Form.useWatch("recurrenceEnabled", form);
  const recurrenceType = Form.useWatch("recurrenceType", form);
  const defaultHostName = currentUser?.displayName || "";
  const attendeeOptions = useMemo(
    () =>
      directoryUsers.map((user) => ({
        value: user.email,
        label: `${user.displayName} (@${user.username})`,
        displayName: user.displayName,
        username: user.username,
        email: user.email,
      })),
    [directoryUsers],
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
      attendees: [],
      startTime: start,
      endTime: start.add(1, "hour"),
      maxOccupants: 30,
      passwordRequired: true,
      recurrenceEnabled: false,
      recurrenceType: "weekly",
      recurrenceInterval: 1,
      recurrenceCount: 12,
    });
  }, [defaultHostName, form, initialValues, mode, open]);

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
      title={mode === "edit" ? "编辑会议" : "创建会议"}
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
            message.error("结束时间必须晚于开始时间");
            return;
          }
          onSubmit(toPayload(values));
        }}
      >
        <Form.Item
          label="会议标题"
          name="title"
          rules={[{ required: true, message: "请输入会议标题" }]}
        >
          <Input placeholder="例如：项目例会" maxLength={80} />
        </Form.Item>

        <Form.Item
          label="主持人"
          name="hostName"
          rules={[{ required: true, message: "请输入主持人" }]}
        >
          <Input placeholder="例如：William Li" maxLength={60} />
        </Form.Item>

        <Form.Item
          label="参会者名单"
          name="attendees"
          extra="请选择补全列表中的具体用户；外部参会者可直接输入完整邮箱"
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
                  selectedOptions[index]?.email || isEmailLike(value) || legacyAttendees.has(String(value)),
              );
              if (validValues.length !== values.length) {
                form.setFieldValue("attendees", validValues);
                message.warning("请选择补全列表中的具体用户，或输入完整邮箱");
              }
            }}
            loading={directoryLoading}
            notFoundContent={directoryLoading ? <Spin size="small" /> : "未找到匹配用户，可直接输入邮箱"}
            optionRender={(option) =>
              option.data.email ? (
                <div className="attendee-option">
                  <span className="attendee-option-name">{option.data.displayName}</span>
                  <span className="attendee-option-meta">
                    @{option.data.username} · {option.data.email}
                  </span>
                </div>
              ) : (
                <div className="attendee-option">
                  <span className="attendee-option-name">{option.label}</span>
                  <span className="attendee-option-meta">
                    {isEmailLike(option.value) ? "按回车添加外部邮箱" : "请从匹配结果中选择具体用户"}
                  </span>
                </div>
              )
            }
            tokenSeparators={[",", ";", "\n"]}
            placeholder="搜索姓名、用户名或邮箱"
            suffixIcon={<Users size={16} />}
            maxTagCount="responsive"
          />
        </Form.Item>

        <Form.Item
          label="开始时间"
          name="startTime"
          rules={[{ required: true, message: "请选择会议时间" }]}
        >
          <DatePicker
            showTime 
            format="YYYY-MM-DD HH:mm"
            placement="topLeft"
            className="full-width"
          />
        </Form.Item>
        <Form.Item
          label="结束时间"
          name="endTime"
          rules={[{ required: true, message: "请选择会议结束时间" }]}
        >
          <DatePicker
            showTime
            format="YYYY-MM-DD HH:mm"
            placement="topLeft"
            className="full-width"
          />
        </Form.Item>
        
          {/* <RangePicker
            showTime
            format="YYYY-MM-DD HH:mm"
            placement="topLeft"
            className="full-width"
          /> */}

        {mode === "create" && (
          <div className="recurrence-section">
            <Form.Item label="周期性会议" name="recurrenceEnabled" valuePropName="checked">
              <Switch checkedChildren="是" unCheckedChildren="否" />
            </Form.Item>

            {recurrenceEnabled && (
              <div className="recurrence-controls">
                <Form.Item
                  label="重复频率"
                  name="recurrenceType"
                  rules={[{ required: true, message: "请选择重复频率" }]}
                >
                  <Select options={recurrenceTypeOptions} />
                </Form.Item>

                {recurrenceType === "every_n_days" && (
                  <Form.Item
                    label="每隔天数"
                    name="recurrenceInterval"
                    rules={[{ required: true, message: "请输入间隔天数" }]}
                  >
                    <InputNumber min={1} max={365} className="full-width" addonAfter="天" />
                  </Form.Item>
                )}

                <Form.Item
                  label="生成场次"
                  name="recurrenceCount"
                  rules={[{ required: true, message: "请输入生成场次" }]}
                >
                  <InputNumber min={2} max={1000} className="full-width" addonAfter="场" />
                </Form.Item>
              </div>
            )}
          </div>
        )}

        <div className="form-grid">
          <Form.Item
            label="最大人数"
            name="maxOccupants"
            rules={[{ required: true, message: "请输入最大人数" }]}
          >
            <InputNumber min={1} max={500} className="full-width" />
          </Form.Item>

          <Form.Item label="创建密码" name="passwordRequired" valuePropName="checked">
            <Switch checkedChildren="是" unCheckedChildren="否" />
          </Form.Item>
        </div>

        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<Plus size={16} />}>
            {mode === "edit" ? "保存" : "创建"}
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
      title={meeting?.password ? "会议访问方式" : "会议已创建"}
      open={Boolean(meeting)}
      onCancel={onClose}
      footer={[
        <Button key="close" type="primary" onClick={onClose}>
          完成
        </Button>,
      ]}
      centered
      width={560}
    >
      {meeting && (
        <div className="result-box">
          {meeting.createdCount && (
            <div className="series-result">
              <Repeat2 size={18} />
              <Text>已创建 {meeting.createdCount} 场周期会议，首场链接如下。</Text>
            </div>
          )}

          <div>
            <Text type="secondary">{protectedMeeting ? "入会验证链接" : "Jitsi 会议链接"}</Text>
            <div className="copy-line">
              <Text className="copy-value">{linkValue}</Text>
              <Button
                icon={<Clipboard size={16} />}
                onClick={() => onCopy(linkValue)}
                aria-label="复制会议链接"
              />
            </div>
          </div>

          {protectedMeeting && meeting.password && (
            <div>
              <Text type="secondary">一次性会议密码</Text>
              <div className="copy-line password-line">
                <Text className="copy-value">{meeting.password}</Text>
                <Button
                  icon={<Clipboard size={16} />}
                  onClick={() => onCopy(meeting.password)}
                  aria-label="复制会议密码"
                />
              </div>
            </div>
          )}

          <Button
            block
            className="share-copy-button"
            icon={<Clipboard size={16} />}
            onClick={() => onCopy(shareText)}
          >
            一键复制分享信息
          </Button>
        </div>
      )}
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
            <Title level={2}>无法打开会议</Title>
            <Text type="secondary">{loadError}</Text>
          </Space>
        ) : meeting ? (
          <Space direction="vertical" size={18} className="join-content">
            <div>
              <Text type="secondary">{meeting.roomId}</Text>
              <Title level={2}>{meeting.title}</Title>
            </div>

            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="主持人">{meeting.hostName}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{displayTime(meeting.startTime)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{displayTime(meeting.endTime)}</Descriptions.Item>
            </Descriptions>

            {meeting.passwordRequired ? (
              <Form form={form} layout="vertical" onFinish={handleJoin} className="join-form">
                <Form.Item
                  label="会议密码"
                  name="password"
                  rules={[{ required: true, message: "请输入会议密码" }]}
                >
                  <Input.Password
                    autoFocus
                    inputMode="numeric"
                    maxLength={4}
                    placeholder="请输入4位数字密码"
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
                >
                  验证并进入
                </Button>
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
              >
                打开 Jitsi 会议
              </Button>
            )}
          </Space>
        ) : null}
      </div>
    </div>
  );
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login");
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
      messageApi.success("申请已提交，请等待管理员审核");
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
      messageApi.success("找回密码申请已提交，请等待管理员处理");
      setMode("login");
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
            <Title level={3}>会议管理系统</Title>
            <Text type="secondary">登录后管理会议预约</Text>
          </div>
        </div>

        <Segmented
          block
          value={mode}
          onChange={setMode}
          options={[
            { label: "登录", value: "login" },
            { label: "申请账号", value: "register" },
            { label: "找回密码", value: "reset" },
          ]}
        />

        {mode === "login" ? (
          <Form layout="vertical" onFinish={handleLogin} className="auth-form">
            <Form.Item
              label="用户名或邮箱"
              name="account"
              rules={[{ required: true, message: "请输入用户名或邮箱" }]}
            >
              <Input autoFocus autoComplete="username" maxLength={120} />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: "请输入密码" }]}
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
            >
              登录
            </Button>
          </Form>
        ) : mode === "register" ? (
          <Form layout="vertical" onFinish={handleRegister} className="auth-form">
            <Form.Item
              label="用户名"
              name="username"
              rules={[
                { required: true, message: "请输入用户名" },
                {
                  pattern: /^[A-Za-z0-9_.-]{3,40}$/,
                  message: "用户名需为 3-40 位字母、数字、点、横线或下划线",
                },
              ]}
            >
              <Input autoFocus autoComplete="username" maxLength={40} />
            </Form.Item>
            <Form.Item
              label="用户昵称（真实姓名）"
              name="displayName"
              rules={[{ required: true, message: "请输入用户昵称（真实姓名）" }]}
            >
              <Input maxLength={80} />
            </Form.Item>
            <Form.Item
              label="邮箱"
              name="email"
              rules={[
                { required: true, message: "请输入邮箱" },
                { type: "email", message: "邮箱格式无效" },
              ]}
            >
              <Input autoComplete="email" maxLength={120} />
            </Form.Item>
            <Form.Item
              label="密码"
              name="password"
              rules={[
                { required: true, message: "请输入密码" },
                { min: 8, message: "密码至少需要 8 位" },
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label="给管理员的留言"
              name="registerMessage"
              rules={[{ required: true, message: "请填写给管理员的留言" }]}
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
            >
              提交申请
            </Button>
          </Form>
        ) : (
          <Form layout="vertical" onFinish={handlePasswordResetRequest} className="auth-form">
            <Form.Item
              label="用户名或邮箱"
              name="account"
              rules={[{ required: true, message: "请输入用户名或邮箱" }]}
            >
              <Input autoFocus autoComplete="username" maxLength={120} />
            </Form.Item>
            <Form.Item
              label="给管理员的说明"
              name="message"
              rules={[{ required: true, message: "请填写找回密码说明" }]}
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
            >
              提交找回申请
            </Button>
          </Form>
        )}
      </div>
    </div>
  );
}

function AdminReviewSection({ pendingUsers, loadingId, onApprove, onReject, onRefresh }) {
  return (
    <section className="side-section review-section">
      <Flex align="center" justify="space-between">
        <Space size={8}>
          <Text strong>账号审核</Text>
          <Badge count={pendingUsers.length} size="small" />
        </Space>
        <Button
          type="text"
          icon={<RefreshCw size={16} />}
          onClick={onRefresh}
          aria-label="刷新账号审核"
        />
      </Flex>
      <List
        size="small"
        dataSource={pendingUsers}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待审核账号" /> }}
        renderItem={(item) => (
          <List.Item>
            <div className="review-line">
              <div className="review-copy">
                <Text strong>{item.displayName}</Text>
                <Text type="secondary">@{item.username}</Text>
                <Text className="review-message">{item.registerMessage}</Text>
              </div>
              <Space size={6} wrap>
                <Button
                  size="small"
                  type="primary"
                  icon={<UserCheck size={14} />}
                  loading={loadingId === item.id}
                  onClick={() => onApprove(item)}
                >
                  通过
                </Button>
                <Popconfirm
                  title="拒绝账号申请"
                  description="被拒绝的账号不能登录系统。"
                  okText="拒绝"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onReject(item)}
                >
                  <Button size="small" danger icon={<XCircle size={14} />} loading={loadingId === item.id}>
                    拒绝
                  </Button>
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
          <Text strong>密码找回</Text>
          <Badge count={requests.length} size="small" />
        </Space>
        <Button
          type="text"
          icon={<RefreshCw size={16} />}
          onClick={onRefresh}
          aria-label="刷新密码找回"
        />
      </Flex>
      <List
        size="small"
        dataSource={requests}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无找回申请" /> }}
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
                >
                  重置
                </Button>
                <Popconfirm
                  title="拒绝找回申请"
                  description="此申请会被标记为已拒绝。"
                  okText="拒绝"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onReject(item)}
                >
                  <Button size="small" danger icon={<XCircle size={14} />} loading={loadingId === item.id}>
                    拒绝
                  </Button>
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
      title={mode === "edit" ? "编辑用户" : "新建用户"}
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
            label="用户名"
            name="username"
            rules={[
              { required: true, message: "请输入用户名" },
              {
                pattern: /^[A-Za-z0-9_.-]{3,40}$/,
                message: "用户名需为 3-40 位字母、数字、点、横线或下划线",
              },
            ]}
          >
            <Input maxLength={40} />
          </Form.Item>
        )}

        <Form.Item
          label="用户昵称（真实姓名）"
          name="displayName"
          rules={[{ required: true, message: "请输入用户昵称（真实姓名）" }]}
        >
          <Input maxLength={80} />
        </Form.Item>

        <Form.Item
          label="邮箱"
          name="email"
          rules={[
            { required: true, message: "请输入邮箱" },
            { type: "email", message: "邮箱格式无效" },
          ]}
        >
          <Input maxLength={120} />
        </Form.Item>

        {mode === "create" && (
          <Form.Item
            label="初始密码"
            name="password"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 8, message: "密码至少需要 8 位" },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        )}

        <div className="form-grid">
          <Form.Item label="角色" name="role" rules={[{ required: true, message: "请选择角色" }]}>
            <Select
              options={[
                { label: "员工", value: "scheduler" },
                { label: "管理员", value: "admin" },
              ]}
            />
          </Form.Item>

          <Form.Item label="状态" name="status" rules={[{ required: true, message: "请选择状态" }]}>
            <Select
              options={[
                { label: "正常", value: "active" },
                { label: "待审核", value: "pending" },
                { label: "已拒绝", value: "rejected" },
                { label: "已停用", value: "disabled" },
              ]}
            />
          </Form.Item>
        </div>

        <Flex justify="end" gap={10} className="modal-actions">
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" htmlType="submit" loading={loading} icon={<UserCheck size={16} />}>
            保存
          </Button>
        </Flex>
      </Form>
    </Modal>
  );
}

function TemporaryPasswordModal({ result, onClose, onCopy }) {
  const temporaryPassword = result?.temporaryPassword;
  return (
    <Modal
      title="临时密码"
      open={Boolean(result)}
      onCancel={onClose}
      footer={[
        <Button key="close" type="primary" onClick={onClose}>
          完成
        </Button>,
      ]}
      centered
      width={520}
    >
      {result && (
        <div className="result-box">
          <Text type="secondary">
            请把临时密码发给 {result.user?.displayName || result.request?.displayName}，对方登录后应尽快在右上角再次修改。
          </Text>
          <div className="copy-line password-line">
            <Text className="copy-value">{temporaryPassword}</Text>
            <Button
              icon={<Clipboard size={16} />}
              onClick={() => onCopy(temporaryPassword)}
              aria-label="复制临时密码"
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
      title="用户管理"
      open={open}
      onClose={onClose}
      width={640}
      extra={
        <Button type="primary" icon={<UserPlus size={16} />} onClick={onCreate}>
          新建用户
        </Button>
      }
    >
      <List
        dataSource={users}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无用户" /> }}
        renderItem={(item) => (
          <List.Item>
            <div className="user-row">
              <div className="user-row-main">
                <Space wrap size={8}>
                  <Text strong>{item.displayName}</Text>
                  <Text type="secondary">@{item.username}</Text>
                  {item.id === currentUser.id && <Tag color="cyan">当前账号</Tag>}
                </Space>
                <Text type="secondary">{item.email || "未填写邮箱"}</Text>
                <Space wrap size={6}>
                  <Tag color={item.role === "admin" ? "gold" : "blue"}>
                    {roleLabels[item.role] || item.role}
                  </Tag>
                  <Tag color={userStatusColors[item.status] || "default"}>
                    {userStatusLabels[item.status] || item.status}
                  </Tag>
                </Space>
              </div>
              <Space size={6} wrap className="user-row-actions">
                <Button size="small" icon={<Edit3 size={14} />} onClick={() => onEdit(item)}>
                  编辑
                </Button>
                <Popconfirm
                  title="重置用户密码"
                  description="系统会生成临时密码并让旧会话失效。"
                  okText="重置"
                  cancelText="取消"
                  onConfirm={() => onResetPassword(item)}
                >
                  <Button size="small" icon={<LockKeyhole size={14} />} loading={loadingId === item.id}>
                    重置密码
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title="删除用户"
                  description="删除后该用户不能再登录系统。"
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => onDelete(item)}
                >
                  <Button size="small" danger icon={<Trash2 size={14} />} loading={loadingId === item.id}>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            </div>
          </List.Item>
        )}
      />
    </Drawer>
  );
}

function ManagementApp({ currentUser, onLogout }) {
  const [meetings, setMeetings] = useState([]);
  const [accessLogs, setAccessLogs] = useState([]);
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
  const [userDrawerOpen, setUserDrawerOpen] = useState(false);
  const [userFormOpen, setUserFormOpen] = useState(false);
  const [userFormMode, setUserFormMode] = useState("create");
  const [selectedUser, setSelectedUser] = useState(null);
  const [temporaryPasswordResult, setTemporaryPasswordResult] = useState(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formMode, setFormMode] = useState("create");
  const [selectedMeeting, setSelectedMeeting] = useState(null);
  const [createdMeeting, setCreatedMeeting] = useState(null);
  const [calendarValue, setCalendarValue] = useState(dayjs());
  const [calendarView, setCalendarView] = useState("week");
  const [messageApi, contextHolder] = message.useMessage();
  const isAdmin = currentUser?.role === "admin";

  const loadData = async ({ silent = false } = {}) => {
    if (!silent) {
      setLoading(true);
    }
    try {
      const requests = [fetchMeetings(), fetchAccessLogs()];
      if (isAdmin) {
        requests.push(fetchUsers(), fetchPasswordResetRequests("pending"));
      }
      const [meetingData, logData, userData, resetData] = await Promise.all(requests);
      setMeetings(meetingData.items || []);
      setAccessLogs(logData.items || []);
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

  const handleApproveUser = async (user) => {
    setReviewingId(user.id);
    try {
      const data = await approveUser(user.id, "scheduler");
      setUsers((items) => items.map((item) => (item.id === user.id ? data.user : item)));
      setPendingUsers((items) => items.filter((item) => item.id !== user.id));
      messageApi.success("账号已通过审核");
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
      messageApi.success("账号申请已拒绝");
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
      messageApi.success("密码已重置");
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
      messageApi.success("找回申请已拒绝");
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
        setUsers((items) => items.map((item) => (item.id === selectedUser.id ? data.user : item)));
        setPendingUsers((items) =>
          data.user.status === "pending"
            ? items.map((item) => (item.id === data.user.id ? data.user : item))
            : items.filter((item) => item.id !== data.user.id),
        );
        messageApi.success("用户已更新");
      } else {
        const data = await createUser(values);
        setUsers((items) => [data.user, ...items]);
        if (data.user.status === "pending") {
          setPendingUsers((items) => [data.user, ...items]);
        }
        messageApi.success("用户已创建");
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
      messageApi.success("用户已删除");
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
      messageApi.success("密码已重置");
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
    messageApi.success("密码已修改，请重新登录");
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

  const meetingsByDate = useMemo(() => {
    const grouped = new Map();
    meetings.forEach((meeting) => {
      const key = dayjs(meeting.startTime).format("YYYY-MM-DD");
      const list = grouped.get(key) || [];
      list.push(meeting);
      grouped.set(
        key,
        list.sort((a, b) => dayjs(a.startTime).valueOf() - dayjs(b.startTime).valueOf()),
      );
    });
    return grouped;
  }, [meetings]);

  const selectedMeetingFresh = useMemo(() => {
    if (!selectedMeeting) return null;
    return meetings.find((meeting) => meeting.id === selectedMeeting.id) || selectedMeeting;
  }, [meetings, selectedMeeting]);

  const summary = useMemo(
    () => ({
      total: meetings.length,
      running: meetings.filter((meeting) => meeting.status === "Running").length,
      scheduled: meetings.filter((meeting) => meeting.status === "Scheduled").length,
      finished: meetings.filter((meeting) => meeting.status === "Finished").length,
    }),
    [meetings],
  );

  const calendarDates = useMemo(
    () => (calendarView === "week" ? buildWeekDates(calendarValue) : buildMonthDates(calendarValue)),
    [calendarValue, calendarView],
  );

  const calendarRangeLabel = useMemo(
    () => formatCalendarRange(calendarValue, calendarView),
    [calendarValue, calendarView],
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

  const handleSubmit = async (payload) => {
    setSaving(true);
    try {
      if (formMode === "edit" && selectedMeetingFresh) {
        const updated = await updateMeeting(selectedMeetingFresh.id, payload);
        setMeetings((items) => items.map((item) => (item.id === updated.id ? updated : item)));
        setSelectedMeeting(updated);
        if (updated.password) {
          setCreatedMeeting(updated);
        }
        showMeetingSaveMessage(messageApi, "会议已更新", updated.emailNotification);
      } else {
        const created = await createMeeting(payload);
        const createdItems = created.seriesMeetings?.length
          ? [created, ...created.seriesMeetings.filter((meeting) => meeting.id !== created.id)]
          : [created];
        setMeetings((items) => [...createdItems, ...items]);
        setCreatedMeeting(created);
        showMeetingSaveMessage(
          messageApi,
          created.createdCount ? `已创建 ${created.createdCount} 场周期会议` : "会议已创建",
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

  const handleDelete = async (meeting, scope = "single") => {
    try {
      const updated = await deleteMeeting(meeting.id, scope);
      const deletedIds = updated.ids || [updated.id];
      setMeetings((items) => items.filter((item) => !deletedIds.includes(item.id)));
      setSelectedMeeting(null);
      messageApi.success(updated.deletedCount ? `已删除 ${updated.deletedCount} 场会议` : "会议已删除");
    } catch (error) {
      messageApi.error(error.message);
    }
  };

  const copyText = async (text) => {
    try {
      await writeClipboardText(text);
      messageApi.success("已复制");
    } catch {
      messageApi.error("复制失败");
    }
  };

  const goToday = () => setCalendarValue(dayjs());
  const goPrevious = () => setCalendarValue((value) => value.subtract(1, calendarView));
  const goNext = () => setCalendarValue((value) => value.add(1, calendarView));
  const selectedMeetingRawLink = selectedMeetingFresh?.passwordRequired
    ? selectedMeetingFresh.accessUrl || selectedMeetingFresh.meetingUrl
    : selectedMeetingFresh?.jitsiUrl || selectedMeetingFresh?.meetingUrl;
  const selectedMeetingLink = absoluteUrl(selectedMeetingRawLink);
  const selectedMeetingLinkLabel = selectedMeetingFresh?.passwordRequired ? "入会验证链接" : "Jitsi 会议链接";

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
          <span>{current.format("M月D日")}</span>
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
            <Title level={3}>会议管理系统</Title>
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
        >
          创建会议
        </Button>

        <div className="summary-grid">
          <div>
            <strong>{summary.total}</strong>
            <span>全部</span>
          </div>
          <div>
            <strong>{summary.scheduled}</strong>
            <span>预约</span>
          </div>
          <div>
            <strong>{summary.running}</strong>
            <span>进行</span>
          </div>
          <div>
            <strong>{summary.finished}</strong>
            <span>结束</span>
          </div>
        </div>

        {isAdmin && (
          <>
            <Button
              block
              icon={<Users size={18} />}
              onClick={() => setUserDrawerOpen(true)}
              className="user-management-button"
            >
              用户管理
            </Button>

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
            <Text strong>最近接入</Text>
            <Button
              type="text"
              icon={<RefreshCw size={16} />}
              onClick={loadData}
              aria-label="刷新"
            />
          </Flex>
          <List
            size="small"
            dataSource={accessLogs}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" /> }}
            renderItem={(item) => (
              <List.Item>
                <div className="log-line">
                  <Badge status={item.success ? "success" : "error"} />
                  <div>
                    <Text>{item.title || item.room_id}</Text>
                    <Text type="secondary">{displayTime(item.join_time)}</Text>
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
            <Title level={2}>会议日历</Title>
            <Text type="secondary">点击会议实体查看链接、参会者和编辑入口</Text>
          </div>
          <Space size={12} className="user-actions">
            <div className="user-pill">
              <Text strong>{currentUser.displayName}</Text>
              <Text type="secondary">{roleLabels[currentUser.role] || currentUser.role}</Text>
            </div>
            <Button icon={<LockKeyhole size={16} />} onClick={() => setPasswordModalOpen(true)}>
              修改密码
            </Button>
            <Button icon={<LogOut size={16} />} loading={loggingOut} onClick={handleLogout}>
              退出
            </Button>
          </Space>
        </Flex>

        <Spin spinning={loading}>
          <section className="meeting-calendar">
            <div className="calendar-toolbar">
              <Space size={8}>
                <Button icon={<ChevronLeft size={16} />} onClick={goPrevious} aria-label="上一页" />
                <Button onClick={goToday}>今天</Button>
                <Button icon={<ChevronRight size={16} />} onClick={goNext} aria-label="下一页" />
              </Space>
              <Text strong className="calendar-range">
                {calendarRangeLabel}
              </Text>
              <Segmented
                value={calendarView}
                onChange={(value) => setCalendarView(value)}
                options={[
                  { label: "周", value: "week" },
                  { label: "月", value: "month" },
                ]}
              />
            </div>

            <div className={`calendar-grid ${calendarView === "week" ? "is-week-view" : "is-month-view"}`}>
              {weekLabels.map((label) => (
                <div key={label} className="calendar-weekday">
                  {label}
                </div>
              ))}
              {calendarDates.map(renderCalendarCell)}
            </div>
          </section>
        </Spin>
      </main>

      <Drawer
        title="会议信息"
        open={Boolean(selectedMeetingFresh)}
        onClose={() => setSelectedMeeting(null)}
        width={520}
        extra={
          selectedMeetingFresh && (
            <Space wrap>
              <Button icon={<Edit3 size={16} />} onClick={() => openEdit(selectedMeetingFresh)}>
                编辑
              </Button>
              <Popconfirm
                title={selectedMeetingFresh.isRecurring ? "删除本场会议" : "删除会议"}
                description="删除后会议会从日历和数据库中移除。"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(selectedMeetingFresh)}
              >
                <Button danger icon={<Trash2 size={16} />}>
                  {selectedMeetingFresh.isRecurring ? "删除本场" : "删除"}
                </Button>
              </Popconfirm>
              {selectedMeetingFresh.isRecurring && (
                <Popconfirm
                  title="删除系列会议"
                  description="此操作会删除同一周期系列下的所有会议。"
                  okText="删除系列"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDelete(selectedMeetingFresh, "series")}
                >
                  <Button danger icon={<Repeat2 size={16} />}>
                    删除系列
                  </Button>
                </Popconfirm>
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
                  {statusMap[selectedMeetingFresh.status]?.text}
                </Tag>
                {selectedMeetingFresh.isRecurring && (
                  <Tag icon={<Repeat2 size={12} />} color="cyan">
                    周期
                  </Tag>
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
                aria-label="复制会议链接"
              />
            </div>

            <Descriptions column={1} size="middle" bordered>
              <Descriptions.Item label="创建者">
                {selectedMeetingFresh.hostName}
              </Descriptions.Item>
              <Descriptions.Item label="开始时间">
                {displayTime(selectedMeetingFresh.startTime)}
              </Descriptions.Item>
              <Descriptions.Item label="结束时间">
                {displayTime(selectedMeetingFresh.endTime)}
              </Descriptions.Item>
              <Descriptions.Item label="会议时长">
                {minutesBetween(selectedMeetingFresh.startTime, selectedMeetingFresh.endTime)}
              </Descriptions.Item>
              <Descriptions.Item label="周期规则">
                {recurrenceText(selectedMeetingFresh)}
              </Descriptions.Item>
              <Descriptions.Item label="最大人数">
                {selectedMeetingFresh.maxOccupants}
              </Descriptions.Item>
              <Descriptions.Item label="会议密码">
                {selectedMeetingFresh.passwordRequired ? "已启用" : "未启用"}
              </Descriptions.Item>
            </Descriptions>

            <section>
              <Flex align="center" gap={8} className="section-title">
                <Users size={18} />
                <Text strong>参会者名单</Text>
              </Flex>
              {selectedMeetingFresh.attendees?.length ? (
                <div className="attendee-list">
                  {selectedMeetingFresh.attendees.map((attendee) => (
                    <Tag key={attendee}>{attendee}</Tag>
                  ))}
                </div>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无参会者" />
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
              {selectedMeetingFresh.passwordRequired ? "打开入会验证页" : "打开 Jitsi 会议"}
            </Button>
          </Space>
        )}
      </Drawer>

      <MeetingFormModal
        open={formOpen}
        mode={formMode}
        initialValues={formMode === "edit" ? selectedMeetingFresh : null}
        currentUser={currentUser}
        loading={saving}
        onCancel={() => setFormOpen(false)}
        onSubmit={handleSubmit}
      />

      <CreationResultModal
        meeting={createdMeeting}
        onClose={() => setCreatedMeeting(null)}
        onCopy={copyText}
      />
      <Modal
        title="修改密码"
        open={passwordModalOpen}
        onCancel={() => {
          setPasswordModalOpen(false);
          passwordForm.resetFields();
        }}
        onOk={() => passwordForm.submit()}
        confirmLoading={passwordChanging}
        okText="确认修改"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={passwordForm} layout="vertical" onFinish={handleChangePassword}>
          <Form.Item
            label="当前密码"
            name="currentPassword"
            rules={[{ required: true, message: "请输入当前密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>

          <Form.Item
            label="新密码"
            name="newPassword"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 8, message: "密码至少需要 8 位" },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>

          <Form.Item
            label="确认新密码"
            name="confirmPassword"
            dependencies={["newPassword"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue("newPassword") === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error("两次输入的新密码不一致"));
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

function ManagementGate() {
  const [currentUser, setCurrentUser] = useState(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadCurrentUser() {
      try {
        const data = await fetchCurrentUser();
        if (active) {
          setCurrentUser(data.user);
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
  }, []);

  if (checking) {
    return (
      <div className="auth-shell">
        <Spin />
      </div>
    );
  }

  if (!currentUser) {
    return <AuthScreen onAuthenticated={setCurrentUser} />;
  }

  return <ManagementApp currentUser={currentUser} onLogout={() => setCurrentUser(null)} />;
}

function App() {
  const joinRoomId = getJoinRoomId();
  if (joinRoomId) {
    return <JoinPage roomId={joinRoomId} />;
  }

  return <ManagementGate />;
}

export default App;
