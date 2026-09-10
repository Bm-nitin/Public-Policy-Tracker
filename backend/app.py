from flask import Flask, request, jsonify
from flask_cors import CORS
from config import Config
from database import init_db
from auth_routes import auth_bp
from chatbot import get_response
from policy_loader import load_policies
import traceback
app = Flask(__name__)

# Non-fatal startup checks (e.g. missing Gemini key, unrestricted CORS,
# unconfigured database). Logs warnings only - never blocks the app from
# starting, preserving the existing graceful-degradation behavior.
Config.validate()

# Allow frontend requests. Defaults to "*" (same as before) unless
# CORS_ORIGINS is set in .env.
CORS(app, resources={r"/*": {"origins": Config.CORS_ORIGINS}})

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


if __name__ == '__main__':
    app.run(host=Config.HOST, port=Config.PORT)