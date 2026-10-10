/*
 * dashboard.js — wires dashboard.html to the Phase 14 backend contract
 * (GET /api/dashboard). No second API, no client-side account state.
 *
 * Authentication: the session lives in an HttpOnly cookie this script can
 * neither read nor write. fetch() uses credentials:"include" so the browser
 * sends it; a 401 means "not signed in" and sends the visitor to login.html
 * WITHOUT rendering anything. Nothing here is stored in localStorage /
 * sessionStorage (the only localStorage use in the app is the theme flag in
 * shared.js).
 *
 * Rendering: every API-derived value is untrusted. The page markup is
 * static in dashboard.html; this file only fills it using createElement /
 * textContent / setAttribute. No innerHTML, eval, or Function anywhere.
 * Links are built only from IDs validated as positive integers.
 *
 * Error handling: raw response bodies and exception messages are never
 * shown or logged — every failure other than 401 collapses to one generic
 * message with a Retry button.
 */
(function () {
  "use strict";

  var API_BASE = "https://policy-tracker-b8a3.onrender.com";
  var DASHBOARD_URL = API_BASE + "/api/dashboard";
  var LOGIN_PAGE = "login.html";
  var RETURN_TO = "dashboard.html"; // allowlisted by auth.js

  var GENERIC_ERROR = "Unable to load your dashboard. Please try again.";

  var ACTIVITY_LABELS = {
    policy_saved: "Saved a policy",
    conversation_created: "Started a conversation",
    message_sent: "Sent a message",
  };

  /* ------------------------------------------------------------------
     PURE HELPERS (also exported for the Node validation script)
     ------------------------------------------------------------------ */

  function toPositiveInt(value) {
    return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
  }

  function toCount(value) {
    return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
  }

  function safeText(value) {
    return typeof value === "string" ? value : "";
  }

  function policyHref(id) {
    var n = toPositiveInt(id);
    return n === null ? null : "policies.html?policy=" + n;
  }

  function conversationHref(id) {
    var n = toPositiveInt(id);
    return n === null ? null : "index.html?conversation=" + n;
  }

  function parseDate(iso) {
    if (typeof iso !== "string" || !iso) return null;
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function formatDateTime(iso) {
    var d = parseDate(iso);
    if (!d) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(d);
    } catch (e) {
      return d.toLocaleString();
    }
  }

  function formatDate(iso) {
    var d = parseDate(iso);
    if (!d) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(d);
    } catch (e) {
      return d.toLocaleDateString();
    }
  }

  // "3 hours ago" for recent items, falling back to a plain date.
  function formatRelative(iso, nowMs) {
    var d = parseDate(iso);
    if (!d) return "";
    var now = typeof nowMs === "number" ? nowMs : Date.now();
    var diffSec = Math.round((d.getTime() - now) / 1000);
    var abs = Math.abs(diffSec);
    if (abs > 7 * 24 * 3600 || typeof Intl === "undefined" || !Intl.RelativeTimeFormat) {
      return formatDate(iso);
    }
    var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (abs < 60) return rtf.format(0, "second");
    if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
    return rtf.format(Math.round(diffSec / 86400), "day");
  }

  // Turns slugs like "culture_tourism" into "Culture Tourism"; leaves
  // already-readable labels alone. Display only — never sent anywhere.
  function formatLabel(raw) {
    var s = safeText(raw).trim();
    if (!s) return "";
    return s.replace(/[_-]+/g, " ").replace(/\b[a-z]/g, function (c) { return c.toUpperCase(); });
  }

  function truncate(text, maxLen) {
    var s = safeText(text);
    return s.length > maxLen ? s.slice(0, maxLen - 1).trimEnd() + "…" : s;
  }

  function initialOf(name) {
    var first = Array.from(safeText(name).trim())[0];
    return first ? first.toUpperCase() : "?";
  }

  function activityLabel(type) {
    return Object.prototype.hasOwnProperty.call(ACTIVITY_LABELS, type) ? ACTIVITY_LABELS[type] : null;
  }

  function activityHref(item) {
    if (item.type === "policy_saved") return policyHref(item.reference_id);
    if (item.type === "conversation_created") return conversationHref(item.reference_id);
    // message_sent: reference_id is a MESSAGE id, not a conversation id,
    // so there is nothing safe to link to.
    return null;
  }

  function plainObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  // Returns a normalised copy of the response, or null if it doesn't match
  // the Phase 14 contract closely enough to render safely.
  function validateDashboard(data) {
    if (!plainObject(data) || !plainObject(data.user) || !plainObject(data.stats)) return null;

    var saved = toCount(data.stats.saved_policies);
    var conversations = toCount(data.stats.conversations);
    var messages = toCount(data.stats.messages);
    if (saved === null || conversations === null || messages === null) return null;

    function list(v) {
      return Array.isArray(v) ? v.filter(plainObject) : [];
    }

    return {
      user: {
        name: safeText(data.user.name),
        email: safeText(data.user.email),
        email_verified: data.user.email_verified === true,
      },
      stats: { saved_policies: saved, conversations: conversations, messages: messages },
      recent_saved_policies: list(data.recent_saved_policies),
      recent_conversations: list(data.recent_conversations),
      recent_activity: list(data.recent_activity),
    };
  }

  /* ------------------------------------------------------------------
     API
     ------------------------------------------------------------------ */

  // Resolves to {kind:"ok", data} | {kind:"unauthorized"} | {kind:"error"}.
  // Never rejects, never exposes the response body.
  function fetchDashboard() {
    return fetch(DASHBOARD_URL, {
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (response.status === 401) return { kind: "unauthorized" };
        if (!response.ok) return { kind: "error" }; // 403 / 404 / 500 / ...
        return response.text().then(function (raw) {
          var parsed;
          try {
            parsed = JSON.parse(raw);
          } catch (e) {
            return { kind: "error" }; // malformed JSON
          }
          var valid = validateDashboard(parsed);
          return valid ? { kind: "ok", data: valid } : { kind: "error" };
        });
      })
      .catch(function () {
        return { kind: "error" }; // network failure
      });
  }

  /* ------------------------------------------------------------------
     DOM HELPERS
     ------------------------------------------------------------------ */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (typeof text === "string") node.textContent = text;
    return node;
  }

  function linkButton(href, label, ariaLabel) {
    var a = el("a", "btn btn-secondary", label);
    a.setAttribute("href", href);
    if (ariaLabel) a.setAttribute("aria-label", ariaLabel);
    return a;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function byId(id) {
    return document.getElementById(id);
  }

  /* ------------------------------------------------------------------
     RENDERING
     ------------------------------------------------------------------ */

  function renderUser(user) {
    byId("dashUserName").textContent = user.name || "Your account";
    byId("dashUserEmail").textContent = user.email;
    byId("dashAvatar").textContent = initialOf(user.name);
    var badge = byId("dashVerified");
    badge.textContent = user.email_verified ? "Email verified" : "Email not verified";
    badge.classList.toggle("is-unverified", !user.email_verified);
  }

  function renderStats(stats) {
    byId("statSaved").textContent = stats.saved_policies.toLocaleString();
    byId("statConversations").textContent = stats.conversations.toLocaleString();
    byId("statMessages").textContent = stats.messages.toLocaleString();
  }

  function buildSavedCard(entry) {
    var policy = entry.policy;
    if (!plainObject(policy)) return null; // backend omits stale ones; be defensive anyway

    var name = safeText(policy.name) || "Unnamed policy";
    var li = el("li", "dash-item");
    li.appendChild(el("h3", "dash-item__title", name));

    var badges = el("div", "dash-item__badges");
    [policy.sector, policy.category, policy.sub_category].forEach(function (value) {
      var label = formatLabel(value);
      if (label) badges.appendChild(el("span", "badge", label));
    });
    if (badges.childNodes.length) li.appendChild(badges);

    [["Change", policy.change], ["Impact", policy.impact]].forEach(function (pair) {
      var text = truncate(pair[1], 140);
      if (!text) return;
      var p = el("p", "dash-item__text");
      p.appendChild(el("strong", "", pair[0] + ": "));
      p.appendChild(document.createTextNode(text));
      li.appendChild(p);
    });

    var saved = formatDate(entry.created_at);
    if (saved) li.appendChild(el("p", "dash-item__meta", "Saved " + saved));

    var href = policyHref(entry.policy_id !== undefined ? entry.policy_id : policy.id);
    if (href) {
      var actions = el("div", "dash-item__actions");
      actions.appendChild(linkButton(href, "View details", "View details for " + name));
      li.appendChild(actions);
    }
    return li;
  }

  function buildConversationCard(conv) {
    var title = safeText(conv.title).trim() || "Untitled conversation";
    var li = el("li", "dash-item");
    li.appendChild(el("h3", "dash-item__title", title));

    var parts = [];
    var updated = formatDateTime(conv.updated_at);
    var created = formatDateTime(conv.created_at);
    if (updated) parts.push("Updated " + updated);
    if (created && created !== updated) parts.push("Started " + created);
    var count = toCount(conv.message_count);
    if (count !== null) parts.push(count + (count === 1 ? " message" : " messages"));
    if (parts.length) li.appendChild(el("p", "dash-item__meta", parts.join(" · ")));

    var href = conversationHref(conv.id);
    if (href) {
      var actions = el("div", "dash-item__actions");
      actions.appendChild(linkButton(href, "Continue", "Continue conversation: " + title));
      li.appendChild(actions);
    }
    return li;
  }

  function buildActivityRow(item) {
    var label = activityLabel(item.type);
    if (!label) return null; // unknown type: never show raw backend terms

    var li = el("li", "dash-activity-row");
    var labelWrap = el("span", "dash-activity-row__label");
    var href = activityHref(item);
    if (href) {
      var a = el("a", "", label);
      a.setAttribute("href", href);
      labelWrap.appendChild(a);
    } else {
      labelWrap.textContent = label;
    }
    li.appendChild(labelWrap);

    var d = parseDate(item.created_at);
    if (d) {
      var time = el("time", "", formatRelative(item.created_at));
      time.setAttribute("datetime", d.toISOString());
      time.setAttribute("title", formatDateTime(item.created_at));
      li.appendChild(time);
    }
    return li;
  }

  // Fills `listEl` from `items` via `builder`; shows `emptyEl` instead when
  // nothing renderable remains.
  function fillList(listEl, emptyEl, items, builder) {
    clear(listEl);
    items.forEach(function (item) {
      var node = builder(item);
      if (node) listEl.appendChild(node);
    });
    var hasItems = listEl.childNodes.length > 0;
    listEl.hidden = !hasItems;
    emptyEl.hidden = hasItems;
  }

  function renderDashboard(data) {
    renderUser(data.user);
    renderStats(data.stats);
    fillList(byId("savedList"), byId("savedEmpty"), data.recent_saved_policies, buildSavedCard);
    fillList(byId("convList"), byId("convEmpty"), data.recent_conversations, buildConversationCard);
    fillList(byId("activityList"), byId("activityEmpty"), data.recent_activity, buildActivityRow);
  }

  /* ------------------------------------------------------------------
     PAGE STATE
     ------------------------------------------------------------------ */

  function showState(state) {
    byId("dashLoading").hidden = state !== "loading";
    byId("dashError").hidden = state !== "error";
    byId("dashContent").hidden = state !== "content";
    byId("dashboardMain").setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }

  function scrubPrivateData() {
    ["dashUserName", "dashUserEmail", "dashAvatar", "dashVerified"].forEach(function (id) {
      byId(id).textContent = "";
    });
    ["savedList", "convList", "activityList"].forEach(function (id) {
      clear(byId(id));
    });
  }

  function showError() {
    scrubPrivateData();
    byId("dashErrorMessage").textContent = GENERIC_ERROR;
    showState("error");
  }

  function redirectToLogin() {
    scrubPrivateData();
    // replace(): the private page must not stay in the back-button history.
    window.location.replace(LOGIN_PAGE + "?next=" + encodeURIComponent(RETURN_TO));
  }

  var loadToken = 0;

  function load() {
    var token = ++loadToken;
    showState("loading");
    fetchDashboard().then(function (result) {
      if (token !== loadToken) return; // a newer load superseded this one
      if (result.kind === "unauthorized") {
        redirectToLogin();
        return;
      }
      if (result.kind !== "ok") {
        showError();
        return;
      }
      try {
        renderDashboard(result.data);
        showState("content");
      } catch (e) {
        showError();
      }
    });
  }

  function init() {
    var retry = byId("dashRetryBtn");
    if (retry) retry.addEventListener("click", load);

    // Back/forward cache can resurrect a page after logout; re-check.
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) load();
    });

    load();
  }

  if (typeof document !== "undefined" && document.getElementById("dashboardMain")) {
    document.addEventListener("DOMContentLoaded", init);
  }

  // Exposed for the offline Node validation script only.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      toPositiveInt: toPositiveInt,
      toCount: toCount,
      policyHref: policyHref,
      conversationHref: conversationHref,
      activityLabel: activityLabel,
      activityHref: activityHref,
      formatLabel: formatLabel,
      formatRelative: formatRelative,
      truncate: truncate,
      initialOf: initialOf,
      validateDashboard: validateDashboard,
    };
  }
})();
