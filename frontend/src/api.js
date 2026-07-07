const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;

  if (!response.ok) {
    throw new Error(payload?.message || "请求失败");
  }

  return payload;
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
