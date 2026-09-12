from flask import Flask, request, jsonify
from flask_cors import CORS
import click
from config import Config
from database import init_db
from auth_routes import auth_bp
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
    try:
        data = request.get_json()

        if not data or 'message' not in data:
            return jsonify({"reply": "Invalid request"}), 400

        user_input = data['message']

        if not user_input.strip():
            return jsonify({"reply": "Empty message"}), 400

        print(f"[INPUT] {user_input}")

        result = get_response(user_input)

        print(f"[OUTPUT] {result}")

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


# Phase 7: `flask import-policies` / `flask import-policies --dry-run`.
# Deliberately a CLI command, not an HTTP route - this is an admin/ops
# action against data/*.json, not something the running application (or
# an anonymous HTTP caller) should be able to trigger.
@app.cli.command("import-policies")
@click.option("--dry-run", is_flag=True, help="Preview the import without writing to the database.")
def import_policies_command(dry_run):
    from import_policies import import_policies_from_json

    if not Config.DATABASE_URL:
        click.echo("DATABASE_URL is not configured - cannot import.")
        return

    report = import_policies_from_json(dry_run=dry_run)

    if report["aborted"]:
        click.echo(f"ABORTED: {report['abort_reason']}")
        validation = report["validation"]
        for entry in validation["malformed_files"]:
            click.echo(f"  malformed file: {entry}")
        for entry in validation["missing_field_records"]:
            click.echo(f"  missing field: {entry}")
        for entry in validation["true_duplicates"]:
            click.echo(f"  true duplicate (name, sector): {entry}")
        return

    mode = "DRY RUN - " if dry_run else ""
    click.echo(
        f"{mode}inserted={report['inserted']} updated={report['updated']} "
        f"skipped={report['skipped']} "
        f"(source total={report['validation']['total_records']} "
        f"across {len(report['validation']['sectors'])} sectors)"
    )


if __name__ == '__main__':
    app.run(host=Config.HOST, port=Config.PORT)