export class AuthError extends Error {}

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") throw new AuthError();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

export const REVIEW_LABEL = {
  awaiting_review: "Awaiting review",
  confirmed: "Confirmed",
  dismissed: "Dismissed",
  escalated: "Escalated",
};
export const RESPONSE_STEPS = [
  ["new", "New"],
  ["dispatched", "Dispatched"],
  ["on_scene", "On scene"],
  ["resolved", "Resolved"],
];

export function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
