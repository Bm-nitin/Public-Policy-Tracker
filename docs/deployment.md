# Deploy Public Policy Tracker on Render and Supabase

Use one Render Python Web Service in Singapore and one Supabase PostgreSQL
project in South Asia (Mumbai), `ap-south-1`. The initial target is a public
demo on both Free plans. Flask serves the existing frontend and API from the
same Render HTTPS origin. The browser calls relative API URLs and receives a
host-only, Secure, HttpOnly session cookie with SameSite=Lax. SQLAlchemy and
the application's own authentication remain in use.

## Review before provisioning

- Land Phase 19B's SMTP TLS and verification-page changes and the Phase 20
  browser workflow on `main`. Review Phase 21, push it through a PR and require
  the backend and browser jobs on `main`. Confirm both actually passed for the
  commit. The Blueprint selects `main` and `checksPass` and disables preview
  environments. Treat the first Blueprint sync, manual deploys, and dashboard
  changes as operator actions and verify checks before invoking them.
  [Render deploy behavior](https://render.com/docs/deploys)
- The runtime files are the versioned `frontend/` assets and `data/` policy
  JSON. The service has no upload directory or persistent local data
  requirement. PostgreSQL stores accounts, sessions, tokens, bookmarks,
  history, optional embeddings, and rate-limit events. Policy embeddings use
  ordinary PostgreSQL text columns. Keep the historical `policies` table.
- Plan the SMTP delivery test before opening the demo for registrations.
  Render Free blocks outbound TCP 25, 465, and 587. The Blueprint uses
  Resend at smtp.resend.com:2587 with verified STARTTLS. Confirm the sender
  domain in Resend and verify actual delivery from Render before opening
  registration. Turn off click tracking for transactional account links.
  [Render Free limits](https://render.com/docs/free)
  [Resend SMTP settings](https://resend.com/docs/send-with-smtp)

## Supabase setup

1. Sign in to the Supabase Dashboard, select a Free organization, and create
   a new project in **South Asia (Mumbai)** under specific regions. Store the
   new database password in a password manager. Use a fresh database dedicated
   to the demo.
   [Available regions](https://supabase.com/docs/guides/platform/regions)
2. In the Data API integration overview, turn off **Enable Data API**. Only
   the Flask service needs SQL access. Under Database settings, enforce SSL
   and download the project's server root certificate.
   [Data API settings](https://supabase.com/docs/guides/api/securing-your-api#disable-the-data-api)
   [PostgreSQL TLS configuration](https://supabase.com/docs/guides/database/connecting-to-postgres#secure-your-connection)
3. Open **Connect**, select **Session pooler**, and copy the endpoint and
   role name. This IPv4 route uses port 5432 and preserves session semantics
   for a persistent Flask process, psycopg prepared statements, and Alembic.
   Keep the dashboard-provided hostname. The Transaction pooler on port
   6543 is designed for short-lived clients and has different prepared
   statement and session behavior. A direct endpoint on Free requires
   verified IPv6 connectivity and is preferred for operator tooling where
   available.
   [Connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres)
   [Supavisor session behavior](https://supabase.com/kb/troubleshooting/supavisor-and-connection-terminology-explained-9pr_ZO)
4. Keep the dashboard's DDL-capable `postgres` credentials with the migration
   operator. Create a separate login role for Render. Run the role statement
   once using an authenticated migration-operator `psql` session. Set the
   password at the interactive prompt and store it in a password manager.

   ```sql
   CREATE ROLE policy_tracker_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
     NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;
   GRANT CONNECT ON DATABASE postgres TO policy_tracker_app;
   GRANT USAGE ON SCHEMA public TO policy_tracker_app;
   \password policy_tracker_app
   ```

   A custom role connecting through the shared pooler uses
   `policy_tracker_app.<project-ref>`. Test the login using session mode
   and confirm `SELECT current_user;` returns `policy_tracker_app`. Keep
   any DDL-capable credentials outside Render and GitHub.
   [Pooler usernames and TLS](https://supabase.com/docs/guides/database/connecting-to-postgres)
   [PostgreSQL roles](https://www.postgresql.org/docs/current/sql-createrole.html)

Use the copied URL in `DATABASE_URL`. Retain its host and port, set the
username for the intended role, and percent-encode reserved characters in
the password. The application accepts `postgresql://` and `postgres://`
and normalizes them to installed psycopg 3. An explicit
`postgresql+psycopg://` also works. Append
`?sslmode=verify-full&sslrootcert=/etc/secrets/supabase-ca.crt` for Render.
Use the certificate's local absolute path for operator commands. If the
copied URL already contains query parameters, keep only one value for
`sslmode`, `sslrootcert`, and optional `application_name`. Verify that the configured certificate
chain validates the exact copied hostname. Resolve TLS or DNS problems
before proceeding.
[Supabase psql instructions](https://supabase.com/docs/guides/database/psql)

The SQLAlchemy pool allows two connections per process, with zero
overflow, a one-second pool wait, a two-second connection timeout,
pre-ping and five-minute recycle. The readiness SQL uses a one-second
statement timeout. One Gunicorn worker gives a budget of
two steady-state connections, with room for the old and new worker during
a rolling deploy and a separate operator connection. Recalculate the
total budget before increasing processes or instances. Session pooler
connections retain a corresponding database connection while checked out
by the application-side pool.
[Supabase pooling and limits](https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits)

## Operator-run migrations and grants

Carry out schema changes before enabling the initial public service or
deploying code that requires a new revision. Use a disposable database to
rehearse the upgrade; resolve drift before applying it to any database
with data. For an existing demo, turn off Render auto-deploy before
merging a schema-changing PR. Apply backward-compatible migrations and
restore auto-deploy after the reviewed release is healthy. Exact revision
checks can remove older instances from rotation after a migration advances
the ledger, so plan for that interval. Take an off-site logical backup and
test-restore it to a disposable database before continuing. Use the
reviewed commit, the migration login, and the same verified TLS settings.
With Render Free, use a trusted operator machine because dashboard shell
and one-off jobs are unavailable.

From a Bash shell in the repository root using Python 3.12.16:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements.txt
export PYTHON_DOTENV_DISABLED=1 APP_ENV=production FLASK_DEBUG=0
export SESSION_COOKIE_SECURE=true SESSION_COOKIE_SAMESITE=Lax RATE_LIMIT_ENABLED=true
read -rp "Canonical frontend HTTPS origin: " FRONTEND_URL
read -rsp "Migration DATABASE_URL (include TLS options): " DATABASE_URL; printf '\n'
read -rsp "Operator CLI secret key (32+ characters): " SECRET_KEY; printf '\n'
export FRONTEND_URL DATABASE_URL SECRET_KEY
python -m flask --app backend/deployment.py db heads
python -m flask --app backend/deployment.py db current
python -m flask --app backend/deployment.py db upgrade
python -m flask --app backend/deployment.py db current
python -m flask --app backend/deployment.py db check
unset DATABASE_URL SECRET_KEY
```

Confirm the current head is `d4f1b7a92c6e` for this release, metadata
check passes, and the historical `public.policies` table exists. Use
the versioned upgrade chain. Treat any unexpected table, revision, or
drift as a reason to stop and investigate before applying further changes.
Gunicorn and the Blueprint never run migrations.

After migrating, execute the runtime grants as the migration operator.
Review this allowlist whenever a migration introduces a new runtime
table or sequence.

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  public.users, public.user_sessions, public.email_verification_tokens,
  public.password_reset_tokens, public.conversations, public.messages,
  public.saved_policies, public.policy_embeddings, public.rate_limit_events
  TO policy_tracker_app;
GRANT USAGE ON SEQUENCE
  public.users_id_seq, public.user_sessions_id_seq, public.email_verification_tokens_id_seq,
  public.password_reset_tokens_id_seq, public.conversations_id_seq, public.messages_id_seq,
  public.saved_policies_id_seq, public.policy_embeddings_id_seq, public.rate_limit_events_id_seq
  TO policy_tracker_app;
GRANT SELECT ON TABLE public.alembic_version TO policy_tracker_app;
```

From the app login, verify it can read `public.alembic_version`, access
application tables, and has no schema CREATE privilege. Verify that
`SHOW search_path;` resolves the application's existing `public` schema.
Use `SELECT has_schema_privilege(current_user, 'public', 'CREATE');` to
check the role's effective permission. If it returns true, review the
project's schema grants, including inherited grants to `PUBLIC`.

## Render setup in the deployment phase

1. Sign in to Render, authorize the GitHub integration for this repository,
   and create a Blueprint pointing to the reviewed `main` branch. Confirm it
   proposes one Singapore Free Python web service, with no other resources.
   Run `render blueprints validate render.yaml` from an authorized Render CLI
   session if you want workspace-aware checks before the controlled sync.
   [Blueprint setup](https://render.com/docs/infrastructure-as-code)
   [Blueprint specification](https://render.com/docs/blueprint-spec)
2. Supply the **app-role** `DATABASE_URL` through Render's secret environment
   field and set `SUPABASE_POOLER_HOST` to the identical dashboard-provided
   hostname, without a scheme or port. On the service Environment page, add `supabase-ca.crt` as a Secret
   File so its runtime path is `/etc/secrets/supabase-ca.crt`. The first sync
   may start the service. Configure the certificate before accepting a
   deployment as healthy. The startup check requires a verified TLS URL,
   and `/ready` confirms actual database connectivity.
   [Render secrets and secret files](https://render.com/docs/configure-environment-variables)
3. Confirm the generated key and the environment below. Enter SMTP settings
   with a verified sender for signup and recovery. Start with Gemini unset
   if you want the deterministic catalog and chat fallback without an AI
   API key. If enabling Gemini, set a dedicated key and set the provider's
   usage limits. Verify the actual `onrender.com` origin against
   `RENDER_EXTERNAL_URL`.
   [Render runtime variables](https://render.com/docs/environment-variables)

| Variable | Production value and purpose |
| --- | --- |
| `DATABASE_URL` | Required secret. App-role Session pooler URL with hostname verification and the CA file. |
| `SECRET_KEY` | Required stable secret of at least 32 characters. Render generates it once for the service. |
| `PYTHON_VERSION`, `PORT` | Blueprint pins Python 3.12.16. Render provides the bind port. |
| `APP_ENV`, `FLASK_DEBUG`, `PYTHON_DOTENV_DISABLED` | `production`, `0`, and `1`. Use Render environment variables and keep debug off. |
| `RENDER_EXTERNAL_URL`, `FRONTEND_URL` | The first is supplied by Render. Optional `FRONTEND_URL` overrides the canonical HTTPS origin, for example after an intentional custom-domain rollout. This origin determines email links and CORS. |
| `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE` | `true` and `Lax`. The existing session service sets HttpOnly. |
| `SUPABASE_POOLER_HOST` | Dashboard-provided shared pooler hostname. The Render app validates the URL host, explicit port 5432, restricted role, and readable CA file. |
| `RENDER` | Set by Render. On the public Free service, IP rate limits use the edge-set `CF-Connecting-IP`. Re-evaluate this trust boundary before enabling private access or adding another proxy. |
| `RATE_LIMIT_ENABLED`, `MAX_REQUEST_BYTES`, `MAX_CHAT_MESSAGE_CHARS` | Keep the production defaults `true`, `65536`, and `2000`. |
| `MAIL_HOST`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_USE_TLS` | Required for working verification and reset. Use Resend with verified STARTTLS on `2587`, username `resend`, a verified sender, and the API key stored as `MAIL_PASSWORD`. |
| `GEMINI_API_KEY` | Optional secret for Gemini features. Flask serves the shipped policy catalog without it. |

The deployment entry point derives `CORS_ORIGINS` and trusted hosts from
the public origin, with the Render hostname also accepted on custom-domain
deployments. Existing CSRF origin enforcement, rate limits, size limits,
authentication and HSTS are applied by Flask. Render terminates HTTPS.
Plain HTTP on the local process is behind its managed edge.

## Deployment and smoke checks

The build and start commands in the Blueprint are:

```bash
python -m pip install --only-binary=:all: -r requirements.txt && python -m pip check
python -m gunicorn --chdir backend --bind "0.0.0.0:$PORT" \
  --workers 1 --worker-class gthread --threads 2 --timeout 60 \
  --forwarded-allow-ips '' --access-logfile - \
  --access-logformat '%(m)s %(U)s %(s)s %(L)s' --error-logfile - \
  'deployment:create_app()'
```

1. Check the deployed commit in Render Events and look in Logs for the
   Gunicorn worker, successful binds, error type and HTTP status. Access
   logs omit query strings. Readiness and Gemini failures log exception
   classes or fixed messages. Keep reset URLs, emails, session cookies and
   credentials out of copied logs.
2. Verify `GET /health` returns HTTP 200 when the process responds, and use
   `GET /ready` to confirm the database and reviewed Alembic head. It returns
   HTTP 503 with a generic status if the database is unreachable, uninitialized,
   or at another revision. Both disable caching. Render uses `/ready` for
   deploys and running instances. Sustained database interruptions can take
   the instance out of rotation and cause a restart. Production auth and
   chat return 503 when the rate-limit store is unavailable.
   [Render health checks](https://render.com/docs/health-checks)
3. Use a dedicated demo account to register, receive and use verification
   mail, sign in, view policies and dashboard, request and complete a reset,
   and sign out. Confirm the UI and API stay on the same origin and session
   cookies are Secure, HttpOnly and SameSite=Lax. Validate real client IP
   handling at Render before opening access. Verification and reset links
   use browser fragments. Each page clears its token from the URL and sends
   it in the POST body. The token pages use `no-store` and `no-referrer`.
   Keep query strings from older reset links out of ingress logging.

## Demo limits, backup, and upgrade

- Supabase Free currently includes 500 MB database storage per project,
  5 GB egress and 5 GB cached egress, and two active Free projects subject
  to its organization/owner rules. Nano uses shared CPU and 500 MB RAM,
  with published maxima of 60 database connections and 200 pooler clients
  and a default pool size of 15. Platform connections use some of the
  database budget. Free projects can pause after about one week of inactivity.
  [Supabase pricing](https://supabase.com/pricing)
  [Compute limits](https://supabase.com/docs/guides/platform/compute-and-disk)
- If Supabase pauses, open the project overview and restore it there,
  then verify session-mode login and `/ready`. Supabase currently documents
  a one-year dashboard restore window. For an older paused project, recover
  from the available export into a new project following Supabase's
  procedure. Once a project is deleted its provider backups are removed.
  Use real demand and operator checks for database traffic.
  [Pause and restore](https://supabase.com/docs/guides/platform/upgrading#time-limits)
- Free lacks managed daily backups and PITR. Keep encrypted, off-site
  logical exports and rehearse restoring them to an isolated database,
  including the Alembic ledger, the legacy policies table, and the role
  grants. Supabase recommends `supabase db dump` with separate schema,
  row data and role exports. For a dedicated app-schema backup, a protected
  local libpq service profile named `policy_tracker_migration` may be used
  with `pg_dump --dbname=service=policy_tracker_migration --schema=public
  --format=custom --no-owner --no-privileges --file=policy-tracker.dump`.
  Verify with `pg_restore --list policy-tracker.dump` and an actual scratch
  restore. A dump made with `--no-privileges` needs the documented runtime
  grants reapplied after restore. Keep exports out of the repository. Prefer a direct operator
  connection when available.
  [Supabase backups](https://supabase.com/docs/guides/platform/backups)
- Render Free spins down after 15 minutes idle. Cold starts take about a
  minute, the filesystem is ephemeral, the workspace has 750 Free instance
  hours per month, and bandwidth/build quotas apply. Supabase remains the
  only persistent data store.
  [Render Free limits](https://render.com/docs/free)
- To upgrade, back up and verify the demo, then increase the Render service
  instance type and the Supabase organization's plan and compute as needed.
  Coordinate the Render plan change with the Blueprint. Supabase Pro removes
  inactivity pausing and currently gives seven days of daily backups.
  Consider PITR when recovery targets require it. Keep the ORM, roles,
  migrations and verified TLS and validate any changed connection URL or
  connection budget before switching traffic.
  [Supabase upgrade and restoration](https://supabase.com/docs/guides/platform/upgrading)
  [Supabase backup retention](https://supabase.com/docs/guides/platform/backups)
