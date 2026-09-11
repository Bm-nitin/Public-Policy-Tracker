/*
 * Phase 5: this file previously implemented a fake, client-only "login" -
 * it wrote {name, password} IN PLAINTEXT to localStorage and never
 * talked to a backend at all (see CURRENT_ARCHITECTURE.md's Phase 0
 * audit). It has been replaced with real calls to the backend's session
 * API (POST /api/auth/register, /login, /logout, GET /api/auth/me).
 *
 * The backend is the only source of truth for whether someone is
 * authenticated - the session itself lives in an HttpOnly cookie this
 * script can't read or forge (that's the point). `credentials: "include"`
 * is required on every call so the browser sends/receives that cookie
 * across the frontend/backend origins.
 *
 * Same element IDs as the previous version (signupName, signupEmail,
 * signupPassword, loginEmail, loginPassword) - login.html itself was not
 * changed.
 */

// Same hardcoded-backend-URL convention already used in script.js -
// not introduced fresh here, and out of scope for Phase 5 to change.
const AUTH_API_BASE = "https://policy-tracker-b8a3.onrender.com/api/auth";

function toggle() {
  document.getElementById("container").classList.toggle("active");
}

/* SIGNUP */
async function signup() {
  const name = document.getElementById("signupName").value;
  const email = document.getElementById("signupEmail").value;
  const password = document.getElementById("signupPassword").value;

  if (!name || !email || !password) {
    alert("All fields are required");
    return;
  }

  try {
    const response = await fetch(`${AUTH_API_BASE}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name, email, password }),
    });
    const data = await response.json();

    if (!response.ok) {
      const detail =
        (data.details && data.details.join(", ")) || data.error || "Signup failed";
      alert(detail);
      return;
    }

    alert(data.message || "Signup successful! Please check your email to verify your account, then log in.");
    toggle();
  } catch (err) {
    alert("Could not reach the server. Please try again.");
  }
}

/* LOGIN */
async function login() {
  const email = document.getElementById("loginEmail").value;
  const password = document.getElementById("loginPassword").value;

  if (!email || !password) {
    alert("Email and password are required");
    return;
  }

  try {
    const response = await fetch(`${AUTH_API_BASE}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    const data = await response.json();

    if (!response.ok) {
      // Same message for "no such account" and "wrong password" - the
      // backend deliberately doesn't distinguish them (see
      // backend/auth_routes.py). The 403 "please verify your email"
      // case is the one intentional exception.
      alert(data.error || "Login failed");
      return;
    }

    window.location.href = "index.html";
  } catch (err) {
    alert("Could not reach the server. Please try again.");
  }
}

/* LOGOUT - not wired to a button in the current HTML, but exposed here
 * so other pages (e.g. index.html) can call it once they add one,
 * without another script re-implementing the fake localStorage pattern
 * this file used to use. */
async function logout() {
  try {
    await fetch(`${AUTH_API_BASE}/logout`, {
      method: "POST",
      credentials: "include",
    });
  } finally {
    window.location.href = "login.html";
  }
}

/* FORGOT PASSWORD (Phase 6) - forgot-password.html */
async function forgotPassword() {
  const emailField = document.getElementById("forgotEmail");
  const statusEl = document.getElementById("forgotStatus");
  if (!emailField || !statusEl) return; // only present on forgot-password.html

  const email = emailField.value;
  if (!email) {
    statusEl.textContent = "Please enter your email.";
    return;
  }

  statusEl.textContent = "Sending...";
  try {
    const response = await fetch(`${AUTH_API_BASE}/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email }),
    });
    const data = await response.json();
    // The backend deliberately returns the SAME generic message whether
    // or not the account exists - shown to the user as-is, not
    // reinterpreted or supplemented with any "does this account exist"
    // logic on the frontend.
    statusEl.textContent = data.message || "If an account with that email exists, a password reset link has been sent.";
  } catch (err) {
    statusEl.textContent = "Could not reach the server. Please try again.";
  }
}

/* RESET PASSWORD (Phase 6) - reset-password.html
 * The raw token travels only in the URL (the credential the user was
 * emailed) - it is read once here and never written to localStorage or
 * sessionStorage. After a successful reset it's cleared from the visible
 * address bar via history.replaceState (a usability/hygiene measure only
 * - the real security controls are server-side expiry, hashing, and
 * single-use enforcement, not hiding the URL). */
async function resetPassword() {
  const newPasswordField = document.getElementById("newPassword");
  const confirmPasswordField = document.getElementById("confirmPassword");
  const statusEl = document.getElementById("resetStatus");
  if (!newPasswordField || !statusEl) return; // only present on reset-password.html

  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (!token) {
    statusEl.textContent = "This reset link is missing its token. Please request a new one.";
    return;
  }

  const newPassword = newPasswordField.value;
  const confirmPassword = confirmPasswordField.value;
  if (!newPassword || newPassword !== confirmPassword) {
    statusEl.textContent = "Passwords do not match.";
    return;
  }

  statusEl.textContent = "Resetting...";
  try {
    const response = await fetch(`${AUTH_API_BASE}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ token, password: newPassword }),
    });
    const data = await response.json();

    if (!response.ok) {
      const detail = (data.details && data.details.join(", ")) || data.error || "Reset failed";
      statusEl.textContent = detail;
      return;
    }

    // Strip the token from the visible URL now that it's been used -
    // hygiene only, not a security boundary (see function docstring).
    history.replaceState(null, "", window.location.pathname);

    statusEl.textContent = (data.message || "Password reset successful.") + " Redirecting to login...";
    // Reset never auto-authenticates (per the backend) - send the user
    // to the normal login page rather than pretending they're signed in.
    setTimeout(() => { window.location.href = "login.html"; }, 2000);
  } catch (err) {
    statusEl.textContent = "Could not reach the server. Please try again.";
  }
}
