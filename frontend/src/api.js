const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;

  if (!response.ok) {
    const error = new Error(payload?.message || "请求失败");
    error.status = response.status;
    throw error;
  }

  return payload;
}

export function fetchCurrentUser() {
  return request("/api/auth/me");
}

export function updateCurrentUserPreferences(values) {
  return request("/api/auth/preferences", {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function login(values) {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function logout() {
  return request("/api/auth/logout", {
    method: "POST",
  });
}

export function changePassword(values) {
  return request("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify(values),
  });

}

export function registerAccount(values) {
  return request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function requestPasswordReset(values) {
  return request("/api/auth/password-reset-requests", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function requestEmailPasswordReset(values) {
  return request("/api/auth/email-password-reset", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function resetPasswordWithToken(values) {
  return request("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function fetchUsers(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/users${query}`);
}

export function searchUserDirectory(query = "") {
  const search = new URLSearchParams();
  if (query.trim()) {
    search.set("q", query.trim());
  }
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return request(`/api/users/directory${suffix}`);
}

export function createUser(values) {
  return request("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function updateUser(id, values) {
  return request(`/api/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function deleteUser(id) {
  return request(`/api/admin/users/${id}`, {
    method: "DELETE",
  });
}

export function resetUserPassword(id) {
  return request(`/api/admin/users/${id}/reset-password`, {
    method: "POST",
  });
}

export function approveUser(id, role = "scheduler") {
  return request(`/api/admin/users/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

export function rejectUser(id) {
  return request(`/api/admin/users/${id}/reject`, {
    method: "POST",
  });
}

export function fetchPasswordResetRequests(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/password-reset-requests${query}`);
}

export function approvePasswordResetRequest(id) {
  return request(`/api/admin/password-reset-requests/${id}/approve`, {
    method: "POST",
  });
}

export function rejectPasswordResetRequest(id) {
  return request(`/api/admin/password-reset-requests/${id}/reject`, {
    method: "POST",
  });
}

export function fetchGroups() {
  return request("/api/groups");
}

export function createGroup(values) {
  return request("/api/groups", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function updateGroup(id, values) {
  return request(`/api/groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function deleteGroup(id) {
  return request(`/api/groups/${id}`, {
    method: "DELETE",
  });
}

export async function addGroupMember(groupId, values) {
  const path = `/api/groups/${groupId}/members`;

  try {
    return await request(path, {
      method: "POST",
      body: JSON.stringify(values),
    });
  } catch (error) {
    const userIds = Array.isArray(values?.userIds) ? values.userIds : [];
    const legacyBackendRejectedBatch =
      error.status === 400 &&
      error.message === "请选择要添加的用户" &&
      userIds.length > 0;

    if (!legacyBackendRejectedBatch) {
      throw error;
    }

    let result = null;
    for (const userId of userIds) {
      result = await request(path, {
        method: "POST",
        body: JSON.stringify({
          userId,
          groupRole: values.groupRole,
        }),
      });
    }
    return result;
  }
}

export function updateGroupMember(groupId, userId, values) {
  return request(`/api/groups/${groupId}/members/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function removeGroupMember(groupId, userId) {
  return request(`/api/groups/${groupId}/members/${userId}`, {
    method: "DELETE",
  });
}

export function fetchMeetings() {
  return request("/api/meetings");
}

export function createCalendarSubscription() {
  return request("/api/calendar/subscription", {
    method: "POST",
  });
}

export function createMeeting(values) {
  return request("/api/meetings", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function updateMeeting(id, values, scope = "single") {
  const query = scope === "single" ? "" : `?scope=${encodeURIComponent(scope)}`;
  return request(`/api/meetings/${id}${query}`, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export function deleteMeeting(id, scope = "single") {
  const query = scope === "single" ? "" : `?scope=${encodeURIComponent(scope)}`;
  return request(`/api/meetings/${id}${query}`, {
    method: "DELETE",
  });
}

export function fetchPublicMeeting(roomId) {
  return request(`/api/public/meetings/${encodeURIComponent(roomId)}`);
}

export function verifyMeetingPassword(roomId, password) {
  return request(`/api/public/meetings/${encodeURIComponent(roomId)}/verify`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}
