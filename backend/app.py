from flask import Flask, request, jsonify
import click
from flask_cors import CORS
from config import Config
from database import init_db
from auth_routes import auth_bp
from policies_routes import policies_bp
from conversations_routes import conversations_bp
from saved_policies_routes import saved_policies_bp
from chatbot import get_response
from policy_loader import load_policies
import traceback
app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

# Non-fatal startup checks (e.g. missing Gemini key, unrestricted CORS,
# unconfigured database). Logs warnings only - never blocks the app from
# starting, preserving the existing graceful-degradation behavior.
Config.validate()

# Allow frontend requests. Defaults to "*" (same as before) unless
# CORS_ORIGINS is set in .env. supports_credentials is only enabled when
# a specific origin is configured - browsers forbid combining a wildcard
# origin with credentialed requests (the login/logout/me cookie), so
# leaving CORS_ORIGINS="*" simply means cross-origin login won't work
# until a real origin is set (Config.validate() warns about this).
_cors_kwargs = {"origins": Config.CORS_ORIGINS}
if Config.CORS_ORIGINS != "*":
    _cors_kwargs["supports_credentials"] = True
CORS(app, resources={r"/*": _cors_kwargs})

# Database (Phase 2 foundation). Only wired up when DATABASE_URL is
# actually set, so the app keeps importing and every pre-Phase-3 route
# keeps working with no database configured at all.
if Config.DATABASE_URL:
    init_db(app, Config.DATABASE_URL)

# Phase 3: registration endpoint (POST /api/auth/register). The blueprint
# itself checks Config.DATABASE_URL before touching db.session, so
# registering it here is safe even when no database is configured - the
# route will just return a 503 for that one endpoint.
app.register_blueprint(auth_bp)

# Phase 8: read-only, database-backed Policy API (GET /api/policies*).
# Same guard pattern as auth_bp - the blueprint's own routes check
# Config.DATABASE_URL before touching the database, so registering it
# here is safe even when no database is configured. Does not touch or
# replace the existing GET /policies (JSON-backed) route below.
app.register_blueprint(policies_bp)

# Phase 12: authenticated persistent chat history (POST/GET
# /api/conversations, GET/DELETE /api/conversations/<id>, GET/POST
# /api/conversations/<id>/messages). Every route requires a valid
# session (sessions.login_required) - registering the blueprint itself
# is always safe; a request without a database configured will fail
# inside sessions.get_current_user() (returns None - see that
# function's docstring) and get a normal 401, same as any other
# unauthenticated request, never a crash.
app.register_blueprint(conversations_bp)

# Phase 13: authenticated saved/bookmarked policies (POST/GET
# /api/saved-policies, GET/DELETE /api/saved-policies/<policy_id>).
# Same always-safe-to-register pattern as conversations_bp above -
# every route requires login, so an unconfigured database just yields
# a normal 401 via sessions.get_current_user(), never a crash.
app.register_blueprint(saved_policies_bp)


# Health check
@app.route('/health')
def health():
    return jsonify({"status": "ok"})


# Home route
@app.route('/')
def home():
    return "Track Public Policy Chatbot is running"


# Chat API
@app.route('/chat', methods=['POST'])
def chat():
    """
    ARCHITECTURE CHANGE (Phase 12, documented per that phase's
    requirement): /chat now accepts an OPTIONAL `conversation_id` in the
    request body. Nothing else about this endpoint's contract changed:

      - No `conversation_id` in the body (the entire pre-Phase-12
        contract - every existing frontend/test call): behavior is
        byte-for-byte unchanged - stateless, unauthenticated, no
        persistence attempted, response shape identical. This is the
        Phase 12 brief's explicitly sanctioned "preserve the existing
        stateless behavior" option, chosen over "always create a new
        conversation" specifically so this remains true.
      - `conversation_id` supplied: the request must be authenticated
        (a valid session cookie - see sessions.get_current_user()) and
        must own that conversation, or this returns 401/404 exactly as
        /api/conversations/<id> does (see conversations_routes.py) -
        never silently falls back to stateless behavior for a bad or
        unauthorized id, which would look like a successful save that
        never happened. On success, the user's message and the
        assistant's reply are both persisted to that conversation
        (backend/conversations_service.py's add_message() - the exact
        same function and ownership/validation path
        POST /api/conversations/<id>/messages uses).

    A database-write failure while persisting (as opposed to an
    ownership/auth failure) never breaks the chat response itself - the
    reply the user asked for is still generated and returned; only the
    persistence step is skipped and logged server-side (see the two
    inner try/except blocks below). get_response() itself never raises
    (every internal failure - retrieval, grounding, Gemini - already
    degrades to a safe fallback string within it; see
    backend/chatbot.py's get_grounded_response()), so "assistant
    generation fails" in the sense of persisting a fabricated message
    cannot happen here: whatever get_response() returns is persisted
    as-is, exactly what was actually returned to the user, never
    anything else.
    """
    try:
        data = request.get_json()

        if not data or 'message' not in data:
            return jsonify({"reply": "Invalid request"}), 400

        user_input = data['message']

        if not user_input.strip():
            return jsonify({"reply": "Empty message"}), 400

        conversation_id_raw = data.get('conversation_id')
        conversation = None
        current_user = None

        if conversation_id_raw is not None:
            from sessions import get_current_user
            current_user = get_current_user()
            if current_user is None:
                return jsonify({"error": "Authentication required"}), 401

            try:
                conversation_id = int(conversation_id_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid conversation_id"}), 400

            from conversations_service import ConversationNotFoundError, get_owned_conversation
            try:
                conversation = get_owned_conversation(current_user.id, conversation_id)
            except ConversationNotFoundError:
                return jsonify({"error": "Conversation not found"}), 404

            try:
                from conversations_service import add_message
                add_message(current_user.id, conversation.id, "user", user_input)
            except Exception:
                # Never let a persistence problem block the reply itself -
                # see the docstring above. Logged server-side only.
                traceback.print_exc()

        print(f"[INPUT] {user_input}")

        result = get_response(user_input)

        print(f"[OUTPUT] {result}")

        if conversation is not None:
            try:
                from conversations_service import add_message
                add_message(current_user.id, conversation.id, "assistant", result)
            except Exception:
                traceback.print_exc()

        return jsonify({"reply": result})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"reply": "Something went wrong"}), 500


# Get all policies
@app.route('/policies', methods=['GET'])
def get_policies():
    try:
        policies = load_policies()
        return jsonify(policies)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Could not load policies"}), 500


# NOTE: the `flask import-policies` CLI command that previously lived here
# has been removed - there is no PostgreSQL policies table left to import
# into (see backend/policy_service.py's module docstring). Validating
# data/*.json is still available via import_policies.validate_json_files()
# if needed (e.g. in CI or manually), it just no longer writes to a
# database.

# Phase 10: `flask generate-embeddings` / `flask generate-embeddings --dry-run`.
# CLI-only, same reasoning as the retired import-policies command above -
# an explicit ops action, never triggered automatically (in particular,
# never on application startup - see the Phase 10 brief's performance
# requirement). Incremental: unchanged policies' embeddings are left
# alone entirely (see backend/embedding_store.py's generate_embeddings()).
@app.cli.command("generate-embeddings")
@click.option("--dry-run", is_flag=True, help="Report what would be generated without calling the embedding provider or writing anything.")
def generate_embeddings_command(dry_run):
    from embedding_store import generate_embeddings

    if not Config.DATABASE_URL:
        click.echo("DATABASE_URL is not configured - cannot store embeddings.")
        return
    if not dry_run and not Config.GEMINI_API_KEY:
        click.echo("GEMINI_API_KEY is not configured - cannot generate embeddings.")
        return

    report = generate_embeddings(dry_run=dry_run)

    mode = "DRY RUN - " if dry_run else ""
    click.echo(
        f"{mode}generated={len(report['generated'])} "
        f"skipped_unchanged={len(report['skipped_unchanged'])} "
        f"failed={len(report['failed'])} "
        f"(of {report['total_policies']} total policies)"
    )
    for entry in report["failed"]:
        click.echo(f"  failed: policy_id={entry['policy_id']} error={entry['error']}")



if __name__ == '__main__':
    app.run(host=Config.HOST, port=Config.PORT)