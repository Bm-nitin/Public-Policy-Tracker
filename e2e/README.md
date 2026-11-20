# Browser E2E tests

Run this suite from the repository root with the Phase 19B frontend and API
changes present. The default pytest command still runs the backend suite.

```powershell
python -m pip install --only-binary=:all: -r requirements.txt -r requirements-dev.txt -r requirements-e2e.txt
python -m playwright install chromium firefox webkit
$env:POLICY_TRACKER_TEST_PG_BIN = (pg_config --bindir)
$env:POLICY_E2E_ARTIFACTS = 'test-results/chromium-failures'
python -m pytest e2e --browser chromium -ra --tb=short `
  --tracing=off --video=off --screenshot=off --output=test-results/chromium-plugin --junitxml=test-results/chromium.xml
$env:POLICY_E2E_ARTIFACTS = 'test-results/smoke-failures'
python -m pytest e2e -m cross_browser --browser firefox --browser webkit -ra --tb=short `
  --tracing=off --video=off --screenshot=off --output=test-results/smoke-plugin --junitxml=test-results/smoke.xml
```

Set `POLICY_TRACKER_TEST_PG_BIN` to the PostgreSQL server's bin directory
if `pg_config` is unavailable. On Linux, use
`python -m playwright install --with-deps chromium firefox webkit` to
install system requirements. Run PostgreSQL as an unprivileged user.

The suite serves the repository frontend and the real Flask application on
separate HTTPS loopback origins. The static test host replaces the hardcoded
API origin in served scripts and maps the existing clean reset URL to its
HTML file. The application uses a migrated disposable PostgreSQL database,
loads two synthetic policies from JSON, and captures test messages instead
of sending email. It uses deterministic assistant text in the real chat
route. Each test gets a fresh browser context and cleared account data.

| Frontend workflow | Backend routes | Data checked |
| --- | --- | --- |
| Sign-up, sign-in and sign-out on `login.html` and the shared navigation | `/api/auth/register`, `/api/auth/login`, `/api/auth/me`, `/api/auth/logout` | User creation, secure session cookie and session revocation. |
| Verification on `verify-email.html` | `/api/auth/verify-email`, `/api/auth/resend-verification` | Verification token consumption and the user's verified flag. |
| Recovery from `login.html` through `reset-password.html` | `/api/auth/forgot-password`, `/api/auth/reset-password` | Reset token consumption, changed password and revoked sessions. |
| Search, sector filter and details on `policies.html` | `/api/policies`, `/api/policies/sectors`, `/api/policies/<id>` | In-memory catalog loaded from fixture JSON. |
| Saved policies on `dashboard.html` and links into `policies.html` | `/api/saved-policies`, `/api/saved-policies/<id>`, `/api/dashboard` | Bookmark rows added and removed through authenticated browser requests. |
| Chat on `index.html` and conversation links on `dashboard.html` | `/chat`, `/api/conversations`, `/api/conversations/<id>/messages`, `/api/dashboard` | Conversation created through an authenticated browser request, persisted messages and history replay. |
| Dashboard and `profile.html` | `/api/dashboard`, `/api/account/profile`, `/api/account/change-password` | Account, conversation and bookmark summaries, display-name updates, changed password and expired sessions. |

The responsive suite tests Chromium at 1366 × 768, 768 × 1024 and 390 × 844.
Firefox and WebKit exercise navigation, sign-in and dashboard smoke tests.

The artifact hook records failure screenshots with inputs, account elements,
and page text containing registered private values masked. Only credential-free
cases marked `public_trace` can retain a trace. Test reports redact registered
credentials and tokens.
Keep automatic Playwright screenshots, videos and traces off when running
credential-bearing workflows. CI uploads only the report directory.

## Current product limitations

- The frontend displays bookmarks and reopens conversations. Bookmark
  creation/removal and starting a persisted conversation have no frontend
  controls. The browser-backed API tests cover those operations.
- Reset email links use an extensionless path that requires a static-host
  rewrite. Reset tokens are currently in the URL query and are cleared
  after a successful reset. The browser checks confirm the token is
  absent from the backend POST URL and its cross-origin referrer. The
  initial frontend request still carries the token in its query.
- Sign-up advertises a six-character minimum. The backend rejects
  passwords shorter than eight characters.
- Loopback uses two origins on the same site. The suite does not establish
  session behavior across production domains, Safari device compatibility,
  real mail delivery, or Gemini behavior.
