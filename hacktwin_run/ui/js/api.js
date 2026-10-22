// Thin JSON client for the HackTwin API. Errors carry the server's message.
export async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}
export const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

let metaPromise = null;
export function meta() { return (metaPromise ||= api("/api/meta")); }
