# Public Policy Tracker

Short project description

## Overview
What the platform does

## Key Features
- Public policy discovery
- Policy database
- Search and filtering
- Deterministic policy retrieval
- AI-assisted responses
- User registration
- Email verification
- Secure login/logout
- Password reset
- Database-backed sessions
- References and official sources
- Dark/light UI [after UI phase]

## Architecture

Frontend
    ↓ HTTPS
Flask Backend
    ↓
PostgreSQL
    ↓
Policy Retrieval
    ↓
Gemini AI fallback/response layer

## Project Structure

frontend/
backend/
data/
migrations/
tests/

## Tech Stack

Frontend
- HTML
- CSS
- JavaScript

Backend
- Python
- Flask
- Flask-SQLAlchemy
- Flask-Migrate

Database
- PostgreSQL

Authentication
- Argon2id
- HttpOnly session cookies
- Email verification
- Password reset

AI
- Google Gemini API

Testing
- pytest

## Policy Dataset

151 policies
15 sectors

Explain current JSON → PostgreSQL migration.

## API Overview

GET /health
POST /chat
GET /policies
GET /api/policies
GET /api/policies/<id>
GET /api/policies/sectors
GET /api/policies/categories

Authentication endpoints

## Authentication Architecture

Explain:
registration
verification
login
sessions
logout
forgot password
reset password

## Retrieval System

Explain Retrieval V2 at a high level.

## Grounded Gemini Response Layer (Phase 11)

`data/*.json` remains the single authoritative source of policy
content - this layer never adds a second copy of policy data anywhere,
it only decides what to show Gemini and how to phrase the question.

**Flow:** `GET /api/chat`'s underlying `get_response()`
(`backend/chatbot.py`) tries a series of fast, deterministic matches
first (category match, direct name similarity, keyword match, then
`hybrid_retrieve()` - deterministic Retrieval V2 plus semantic search,
see `backend/retrieval.py`). Only when none of those find anything does
it fall through to the grounded response layer:

```
user query
    -> hybrid_retrieve()                         (backend/retrieval.py)
    -> retrieved policy records
    -> grounding context builder                 (backend/grounding.py)
    -> Gemini 2.5 Flash, via a dedicated
       system_instruction (never mixed with
       user input or policy text)                (backend/gemini_service.py)
    -> grounded response
```

Each layer has one job (`backend/chatbot.py` only orchestrates the
call sequence and decides what to do with each outcome; it contains no
retrieval, context-building, or Gemini-request logic of its own):

- `backend/retrieval.py` - deterministic and hybrid retrieval only.
- `backend/semantic_retrieval.py` - embedding-based semantic search only
  (Phase 10).
- `backend/grounding.py` - converts retrieved policy dicts into a
  bounded, deterministic context string (`POLICY 1 / Name / Sector /
  Category / Sub-category / Change / Impact`, one block per retrieved
  policy, capped at 5 policies / ~6000 characters) and holds the fixed
  grounding system instruction. Pure string logic - fully testable
  without ever calling Gemini.
- `backend/gemini_service.py` - the only module that actually talks to
  Gemini for this layer, via the existing `google-genai` client and
  `GEMINI_API_KEY`/`GEMINI_MODEL` (`gemini-2.5-flash`) configuration -
  no second provider, no second API key.

**Grounding discipline:** Gemini is told, via a dedicated
`system_instruction` (a separate, privileged API parameter - never
string-concatenated with the user's question or with policy content,
so neither can override it) to answer using *only* the supplied policy
context, never invent facts, names, dates, eligibility criteria,
benefits, application procedures, or URLs not present in that context,
and to say plainly when the context is insufficient. Only the fields
that actually exist on a policy record - name, sector, category,
sub_category, change, impact - are ever included; nothing is fabricated
to fill out a richer-looking context.

**Fallback behavior (Gemini failure must never break the chatbot):**
- If `hybrid_retrieve()` finds nothing at all, Gemini is never called
  with an empty context - a fixed "not enough information" message is
  returned directly.
- If Gemini is unavailable (no `GEMINI_API_KEY`, network/provider
  failure, empty response, or any other error), the response falls back
  to the same deterministic, retrieved-policy-based formatting the
  fast-match paths already use - never a raw exception, stack trace, or
  API key reaches the user.

## Persistent Chat History (Phase 12)

Authenticated users can persist their conversations to PostgreSQL. This
is layered on top of everything above it without changing any of it:
`data/*.json` is still the only source of policy content - a persisted
message's optional `metadata` may point at policy ids (e.g.
`{"retrieval_mode": "hybrid", "policy_ids": [12, 45, 78]}`) but never
duplicates a policy's name/category/change/impact into the database.

**Schema:** two new tables, `conversations` (`user_id -> users.id`) and
`messages` (`conversation_id -> conversations.id`, cascading delete),
each with the indexes needed for their access patterns
(`conversations.user_id`, `conversations.updated_at`,
`messages.conversation_id`, `messages.created_at`). Nothing about
`users`, sessions, or `policy_embeddings` changed.

**Ownership model:** every endpoint requires a valid session
(`sessions.login_required` - the same mechanism `/api/auth/me` already
uses) and re-verifies, on every single request, that the requesting
user owns the conversation being accessed - never trusting a
conversation id supplied by the client beyond "look this up and check
who it belongs to." A conversation that doesn't exist and one that
belongs to someone else are always indistinguishable from the outside
(both a plain 404), so the API never reveals whether another user's
conversation exists.

**Endpoints** (all under `/api/conversations`, all authenticated):
`POST /` (create - optional `title`, or a `first_message` to derive one
from, deterministically, without a Gemini call), `GET /` (paginated,
newest-updated-first), `GET /<id>`, `DELETE /<id>`, `GET /<id>/messages`
(paginated, oldest-first), `POST /<id>/messages` (`role` restricted to
`user`/`assistant`; validated length; optional lightweight `metadata`).

**`/chat` integration:** unchanged when no `conversation_id` is sent in
the body (the entire pre-Phase-12 contract) - still stateless,
unauthenticated, identical response shape. Sending an owned
`conversation_id` persists both the user's message and the assistant's
reply to it; a missing/unowned one returns 401/404 rather than silently
falling back to stateless behavior. A database error while persisting
never blocks the reply itself - the chat answer is still generated and
returned even if saving it fails.

## Saved (Bookmarked) Policies (Phase 13)

Authenticated users can bookmark policies. Exactly like Phase 10's
`policy_embeddings` and Phase 12's `messages.metadata`, PostgreSQL here
stores only a *relationship* - `saved_policies` has no name/category/
sub_category/change/impact column at all, so there is nothing to
duplicate or let go stale relative to `data/*.json`. Every read
resolves `policy_id` against the current JSON dataset at request time,
so a saved policy's displayed details always reflect the live dataset.

**Schema:** `saved_policies` (`id`, `user_id -> users.id`, `policy_id`
- no FK, same reasoning as `policy_embeddings.policy_id` - there is no
`policies` table to reference, `created_at`), with a genuine
`UNIQUE(user_id, policy_id)` database constraint (not just an
application-level check) preventing duplicate saves, enforced at both
layers: an app-level pre-check for the common case, and the
database constraint itself as the actual source of truth for the race-
condition window between a check and an insert - saving a policy twice
resolves idempotently to the existing bookmark rather than erroring.

**Endpoints** (all under `/api/saved-policies`, all authenticated):
`POST /` (body: `{"policy_id": 42}`; 404 if that id has no current
policy; returns the bookmark plus current policy data), `GET /`
(paginated, newest-saved-first), `GET /<policy_id>`, `DELETE
/<policy_id>` (deletes only the caller's own relationship - another
user's bookmark for the same policy is untouched). Ownership always
comes from the authenticated session, never from the request body - a
client-supplied `user_id` has no effect.

## Dashboard (Phase 14)

A single authenticated endpoint aggregating a user's existing data -
**no new database table**: every value is read from `users`,
`conversations`, `messages`, and `saved_policies` (Phases 3, 12, 13) or
resolved from `data/*.json`, never duplicated anywhere.

**`GET /api/dashboard`** (requires login, same session mechanism as
every other authenticated endpoint). Identity always comes from
`g.current_user.id` - a `user_id` in the query string is read nowhere
in the route and has no effect.

Response:
```json
{
  "user": {"id": ..., "name": ..., "email": ..., "email_verified": ..., "created_at": ...},
  "stats": {"saved_policies": ..., "conversations": ..., "messages": ...},
  "recent_saved_policies": [{"id": ..., "policy_id": ..., "created_at": ..., "policy": {...current JSON fields...}}],
  "recent_conversations": [{"id": ..., "title": ..., "created_at": ..., "updated_at": ..., "message_count": ...}],
  "recent_activity": [{"type": "policy_saved" | "conversation_created" | "message_sent", "created_at": ..., "reference_id": ...}]
}
```
`password_hash` and every session/verification/reset-token table are
never read by this endpoint at all. `recent_activity` never includes
message content - only its type, timestamp, and id.

**Limits:** optional `?saved_limit=`, `?conversation_limit=`,
`?activity_limit=` query params - default 5 (5/5/10 for saved policies/
conversations/activity respectively), maximum 20, rejecting zero,
negative, and malformed values with a clean 400.

**JSON source of truth:** `recent_saved_policies` resolves each policy
against the current `data/*.json` load on every request - a stale or
since-edited policy is never returned from a cached copy, and a saved
policy whose JSON entry was removed entirely is silently omitted from
the list (the underlying bookmark itself is untouched and still
deletable via Phase 13's API).

**Authorization:** every query is scoped to the authenticated user's
own rows - another user's saved policies, conversations, messages, and
counts are never visible, regardless of what a client sends.

## Configuration

.env
DATABASE_URL
GEMINI_API_KEY
etc.

## Local Development

Backend setup
Database setup
Migrations
Import policies
Run application
Run tests

## Testing

pytest -q

## Deployment

Frontend
Backend
Database

## Security

Password hashing
session security
token hashing
single-use tokens
CORS
etc.

## Limitations

Policy data freshness
AI limitations
academic project

## Roadmap

Completed
Current
Planned

## Disclaimer

Academic/educational project
Verify important policy information with official sources.

## License