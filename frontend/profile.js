/*
 * profile.js - wires profile.html to the Phase 17 account endpoints:
 *   GET   /api/account/profile
 *   PATCH /api/account/profile          { name }
 *   POST  /api/account/change-password  { current_password, new_password }
 *
 * Authentication: the session is an HttpOnly cookie this script can neither
 * read nor write; fetch() uses credentials:"include". A 401 means "no valid
 * session" and sends the visitor to login.html without rendering anything.
 *
 * Passwords: they exist only in the three <input> elements. They are sent
 * once, over the request body, and never copied into JS variables that
 * outlive the submit handler, storage, URLs, logs, or the DOM as text. No
 * localStorage / sessionStorage / cookie is touched by this file.
 *
 * Rendering: markup is static in profile.html; this file only sets
 * textContent / .value / attributes. No innerHTML, eval or Function.
 * Server text shown to the user is limited to our own validation messages
 * from 400 responses (plain strings, length-capped, rendered as text);
 * everything else (5xx, network, malformed JSON) is one generic message.
 *
 * Client-side validation mirrors backend/validators.py (name <= 120, not
 * blank; password 8-128) for fast feedback only - the server is the
 * authority and re-validates everything.
 */
(function () {
  "use strict";

  var API_BASE = "https://policy-tracker-b8a3.onrender.com";
  var PROFILE_URL = API_BASE + "/api/account/profile";
  var PASSWORD_URL = API_BASE + "/api/account/change-password";
  var LOGIN_PAGE = "login.html";
  var RETURN_TO = "profile.html"; // allowlisted by auth.js

  var MAX_NAME = 120;
  var MIN_PASSWORD = 8;
  var MAX_PASSWORD = 128;

  var LOAD_ERROR = "Unable to load your account. Please try again.";
  var SAVE_ERROR = "Something went wrong. Please try again.";
  var NETWORK_ERROR = "Couldn't reach the server. Check your connection and try again.";

  // Phase 18: the backend rate-limits wrong-password attempts (429).
  function limitedMessage(seconds) {
    var minutes = Math.max(1, Math.ceil((typeof seconds === "number" && isFinite(seconds) ? seconds : 60) / 60));
    return "Too many attempts. Please try again in about " + minutes + (minutes === 1 ? " minute." : " minutes.");
  }

  /* ------------------------------------------------------------------
     PURE HELPERS (also exported for the Node validation script)
     ------------------------------------------------------------------ */

  function plainObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  function validateName(raw) {
    var value = typeof raw === "string" ? raw.trim() : "";
    if (!value) return { value: value, error: "Enter a display name." };
    if (value.length > MAX_NAME) {
      return { value: value, error: "Display name must be " + MAX_NAME + " characters or fewer." };
    }
    return { value: value, error: null };
  }

  // Returns {current, next, confirm, form} - each null or a message.
  function validatePasswordChange(current, next, confirm) {
    var out = { current: null, next: null, confirm: null };
    if (!current) out.current = "Enter your current password.";
    if (!next) {
      out.next = "Enter a new password.";
    } else if (next.length < MIN_PASSWORD) {
      out.next = "New password must be at least " + MIN_PASSWORD + " characters.";
    } else if (next.length > MAX_PASSWORD) {
      out.next = "New password must be " + MAX_PASSWORD + " characters or fewer.";
    } else if (current && next === current) {
      out.next = "New password must be different from your current password.";
    }
    if (!confirm) {
      out.confirm = "Confirm your new password.";
    } else if (next && confirm !== next) {
      out.confirm = "Passwords don't match.";
    }
    return out;
  }

  function hasErrors(errs) {
    return Object.keys(errs).some(function (k) { return errs[k]; });
  }

  // Normalised profile, or null if the response doesn't match the contract.
  function validateProfile(data) {
    if (!plainObject(data) || !plainObject(data.profile)) return null;
    var p = data.profile;
    if (typeof p.name !== "string" || typeof p.email !== "string" || typeof p.email_verified !== "boolean") {
      return null;
    }
    return {
      name: p.name,
      email: p.email,
      email_verified: p.email_verified,
      created_at: typeof p.created_at === "string" ? p.created_at : null,
    };
  }

  function formatDate(iso) {
    if (typeof iso !== "string" || !iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(d);
    } catch (e) {
      return d.toLocaleDateString();
    }
  }

  // Only plain, short strings from a 400 response are ever shown.
  function safeMessages(body) {
    var out = [];
    if (!plainObject(body)) return out;
    if (Array.isArray(body.details)) {
      body.details.forEach(function (d) {
        if (typeof d === "string" && d.length <= 200 && out.length < 5) out.push(d);
      });
    }
    if (!out.length && typeof body.error === "string" && body.error.length <= 200) out.push(body.error);
    return out;
  }

  /* ------------------------------------------------------------------
     API  -> {kind:"ok",data} | {kind:"unauthorized"} | {kind:"invalid",messages}
             | {kind:"error", network?:true}.  Never rejects, never exposes bodies.
     ------------------------------------------------------------------ */

  function request(method, url, payload) {
    var options = { method: method, credentials: "include", headers: { Accept: "application/json" } };
    if (payload !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    return fetch(url, options)
      .then(function (response) {
        if (response.status === 401) return { kind: "unauthorized" };
        return response.text().then(function (raw) {
          var parsed = null;
          try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }
          if (response.status === 400) return { kind: "invalid", messages: safeMessages(parsed) };
          if (response.status === 429) {
            return { kind: "limited", retryAfter: plainObject(parsed) ? parsed.retry_after : null };
          }
          if (!response.ok) return { kind: "error" };
          if (!plainObject(parsed)) return { kind: "error" };
          return { kind: "ok", data: parsed };
        });
      })
      .catch(function () {
        return { kind: "error", network: true };
      });
  }

  /* ------------------------------------------------------------------
     DOM
     ------------------------------------------------------------------ */

  function byId(id) { return document.getElementById(id); }

  function showState(state) {
    byId("accLoading").hidden = state !== "loading";
    byId("accError").hidden = state !== "error";
    byId("accContent").hidden = state !== "content";
    byId("accountMain").setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }

  function setFieldError(inputId, errorId, message) {
    var input = byId(inputId);
    var el = byId(errorId);
    if (message) {
      el.textContent = message;
      el.hidden = false;
      input.setAttribute("aria-invalid", "true");
    } else {
      el.textContent = "";
      el.hidden = true;
      input.removeAttribute("aria-invalid");
    }
  }

  function setStatus(id, kind, message) {
    var el = byId(id);
    el.textContent = message || "";
    el.classList.toggle("is-success", kind === "success");
    el.classList.toggle("is-error", kind === "error");
  }

  function setFormError(message) {
    var el = byId("passwordFormError");
    el.textContent = message || "";
    el.hidden = !message;
  }

  function setBusy(buttonId, busy, idleLabel, busyLabel) {
    var btn = byId(buttonId);
    btn.disabled = busy;
    btn.textContent = busy ? busyLabel : idleLabel;
  }

  function clearPasswordFields() {
    ["accCurrentPw", "accNewPw", "accConfirmPw"].forEach(function (id) {
      var input = byId(id);
      input.value = "";
      input.type = "password";
      input.removeAttribute("aria-invalid");
    });
    byId("accShowPw").checked = false;
  }

  function renderProfile(profile) {
    byId("accEmail").textContent = profile.email;
    var badge = byId("accVerified");
    badge.textContent = profile.email_verified ? "Verified" : "Not verified";
    badge.classList.toggle("is-unverified", !profile.email_verified);
    var since = formatDate(profile.created_at);
    byId("accSince").textContent = since || "-";
    byId("accName").value = profile.name;
  }

  // The header (shared.js) shows the name it fetched at page load; keep it in step.
  function syncHeaderName(name) {
    var el = document.querySelector(".app-header__username");
    if (el) el.textContent = name;
  }

  /* ------------------------------------------------------------------
     STATE + FLOWS
     ------------------------------------------------------------------ */

  var currentProfile = null;
  var loadToken = 0;

  function redirectToLogin() {
    clearPasswordFields();
    // replace(): the private page must not stay in back-button history.
    window.location.replace(LOGIN_PAGE + "?next=" + encodeURIComponent(RETURN_TO));
  }

  function showLoadError() {
    currentProfile = null;
    clearPasswordFields();
    byId("accName").value = "";
    ["accEmail", "accVerified", "accSince"].forEach(function (id) { byId(id).textContent = ""; });
    byId("accErrorMessage").textContent = LOAD_ERROR;
    showState("error");
  }

  function load() {
    var token = ++loadToken;
    showState("loading");
    request("GET", PROFILE_URL).then(function (result) {
      if (token !== loadToken) return;
      if (result.kind === "unauthorized") return redirectToLogin();
      var profile = result.kind === "ok" ? validateProfile(result.data) : null;
      if (!profile) return showLoadError();
      try {
        currentProfile = profile;
        renderProfile(profile);
        setStatus("profileStatus", null, "");
        setStatus("passwordStatus", null, "");
        showState("content");
      } catch (e) {
        showLoadError();
      }
    });
  }

  function onProfileSubmit(event) {
    event.preventDefault();
    var check = validateName(byId("accName").value);
    setFieldError("accName", "accNameError", check.error);
    setStatus("profileStatus", null, "");
    if (check.error) { byId("accName").focus(); return; }
    if (currentProfile && check.value === currentProfile.name) {
      setStatus("profileStatus", "success", "No changes to save.");
      return;
    }

    setBusy("accNameSave", true, "Save changes", "Saving…");
    request("PATCH", PROFILE_URL, { name: check.value }).then(function (result) {
      setBusy("accNameSave", false, "Save changes", "Saving…");
      if (result.kind === "unauthorized") return redirectToLogin();
      if (result.kind === "ok") {
        var profile = validateProfile(result.data);
        if (profile) {
          currentProfile = profile;
          byId("accName").value = profile.name;
          syncHeaderName(profile.name);
        }
        setStatus("profileStatus", "success", "Profile updated.");
        return;
      }
      if (result.kind === "invalid") {
        var msg = result.messages[0] || SAVE_ERROR;
        setFieldError("accName", "accNameError", msg);
        byId("accName").focus();
        return;
      }
      setStatus("profileStatus", "error", result.kind === "limited" ? limitedMessage(result.retryAfter) : result.network ? NETWORK_ERROR : SAVE_ERROR);
    });
  }

  function onPasswordSubmit(event) {
    event.preventDefault();
    setStatus("passwordStatus", null, "");
    setFormError("");

    var current = byId("accCurrentPw").value;
    var next = byId("accNewPw").value;
    var confirm = byId("accConfirmPw").value;

    var errs = validatePasswordChange(current, next, confirm);
    setFieldError("accCurrentPw", "accCurrentPwError", errs.current);
    setFieldError("accNewPw", "accNewPwError", errs.next);
    setFieldError("accConfirmPw", "accConfirmPwError", errs.confirm);
    if (hasErrors(errs)) {
      var first = errs.current ? "accCurrentPw" : errs.next ? "accNewPw" : "accConfirmPw";
      byId(first).focus();
      return;
    }

    setBusy("accPasswordSave", true, "Update password", "Updating…");
    request("POST", PASSWORD_URL, { current_password: current, new_password: next }).then(function (result) {
      setBusy("accPasswordSave", false, "Update password", "Updating…");
      if (result.kind === "unauthorized") return redirectToLogin();
      if (result.kind === "ok") {
        clearPasswordFields();
        ["accCurrentPw", "accNewPw", "accConfirmPw"].forEach(function (id) { byId(id).removeAttribute("aria-invalid"); });
        setFieldError("accCurrentPw", "accCurrentPwError", null);
        setFieldError("accNewPw", "accNewPwError", null);
        setFieldError("accConfirmPw", "accConfirmPwError", null);
        setStatus("passwordStatus", "success", "Password updated. Your other signed-in devices have been signed out.");
        return;
      }
      if (result.kind === "invalid") {
        var msg = result.messages[0] || SAVE_ERROR;
        if (/current password/i.test(msg)) {
          setFieldError("accCurrentPw", "accCurrentPwError", msg);
          byId("accCurrentPw").focus();
        } else {
          setFormError(msg);
        }
        return;
      }
      setStatus("passwordStatus", "error", result.kind === "limited" ? limitedMessage(result.retryAfter) : result.network ? NETWORK_ERROR : SAVE_ERROR);
    });
  }

  function onShowPasswordsChange() {
    var type = byId("accShowPw").checked ? "text" : "password";
    ["accCurrentPw", "accNewPw", "accConfirmPw"].forEach(function (id) { byId(id).type = type; });
  }

  function init() {
    byId("accRetryBtn").addEventListener("click", load);
    byId("profileForm").addEventListener("submit", onProfileSubmit);
    byId("passwordForm").addEventListener("submit", onPasswordSubmit);
    byId("accShowPw").addEventListener("change", onShowPasswordsChange);

    // Back/forward cache: never resurrect typed passwords or stale account data.
    window.addEventListener("pagehide", clearPasswordFields);
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) { clearPasswordFields(); load(); }
    });

    load();
  }

  if (typeof document !== "undefined" && document.getElementById("accountMain")) {
    document.addEventListener("DOMContentLoaded", init);
  }

  // Exposed for the offline Node validation script only.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      validateName: validateName,
      validatePasswordChange: validatePasswordChange,
      validateProfile: validateProfile,
      safeMessages: safeMessages,
      limitedMessage: limitedMessage,
      formatDate: formatDate,
    };
  }
})();
