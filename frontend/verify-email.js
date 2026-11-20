(() => {
  const verifyForm = document.getElementById("verifyEmailForm");
  const resendForm = document.getElementById("resendVerificationForm");
  const status = document.getElementById("verificationStatus");
  const signIn = document.getElementById("verificationSignIn");

  let token;

  function readVerificationLink() {
    token = new URLSearchParams(window.location.hash.slice(1)).get("token")
      || new URLSearchParams(window.location.search).get("token");
    if (token) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    verifyForm.hidden = !token;
    resendForm.hidden = Boolean(token);
    status.textContent = "";
    signIn.textContent = "Back to sign in";
  }

  readVerificationLink();
  window.addEventListener("hashchange", readVerificationLink);

  function postAuth(path, body) {
    return fetch(`${AUTH_API_BASE}/${path}`, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  verifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = verifyForm.querySelector("button");
    if (!token || submit.disabled) return;
    const submittedToken = token;
    submit.disabled = true;
    status.textContent = "Verifying your email…";

    try {
      const response = await postAuth("verify-email", { token: submittedToken });
      if (submittedToken !== token) return;
      if (response.ok) {
        token = null;
        verifyForm.hidden = true;
        status.textContent = "Your email is verified. You can now sign in.";
        signIn.textContent = "Continue to sign in";
        signIn.focus();
      } else if (response.status === 400) {
        token = null;
        verifyForm.hidden = true;
        resendForm.hidden = false;
        status.textContent = "This link is invalid, expired, or already used. Sign in or request a new link.";
      } else {
        status.textContent = response.status === 429
          ? "Too many attempts. Please wait and try again."
          : "Email verification is temporarily unavailable. Please try again.";
      }
    } catch {
      if (submittedToken === token) {
        status.textContent = "Could not reach the server. Please try again.";
      }
    } finally {
      submit.disabled = false;
    }
  });

  resendForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = resendForm.querySelector("button");
    if (submit.disabled) return;
    submit.disabled = true;
    const email = document.getElementById("verificationEmail").value.trim();
    status.textContent = "Requesting a new link…";

    try {
      const response = await postAuth("resend-verification", { email });
      if (resendForm.hidden) return;
      if (response.ok) {
        status.textContent = "If an unverified account exists for that email, a verification link has been sent.";
      } else {
        status.textContent = response.status === 429
          ? "Too many requests. Please wait and try again."
          : "Could not request a verification link. Please try again.";
      }
    } catch {
      if (!resendForm.hidden) {
        status.textContent = "Could not reach the server. Please try again.";
      }
    } finally {
      submit.disabled = false;
    }
  });
})();
