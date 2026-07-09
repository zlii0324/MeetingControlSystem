const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

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

export function fetchUsers(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request(`/api/admin/users${query}`);
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

export function fetchMeetings() {
  return request("/api/meetings");
}

export function createMeeting(values) {
  return request("/api/meetings", {
    method: "POST",
    body: JSON.stringify(values),
  });
}

export function updateMeeting(id, values) {
  return request(`/api/meetings/${id}`, {
    method: "PUT",
    body: JSON.stringify(values),
  });
}

export function deleteMeeting(id, scope = "single") {
  const query = scope === "series" ? "?scope=series" : "";
  return request(`/api/meetings/${id}${query}`, {
    method: "DELETE",
  });
}

export function fetchAccessLogs() {
  return request("/api/access-logs?limit=20");
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
