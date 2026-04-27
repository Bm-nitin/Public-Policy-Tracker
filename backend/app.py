from flask import Flask, request, jsonify
from flask_cors import CORS
from chatbot import get_response
from policy_loader import load_policies
import traceback

app = Flask(__name__)

# Allow frontend requests
CORS(app, resources={r"/*": {"origins": "*"}})


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

        print(f"User Input: {user_input}")

        # Call chatbot
        result = get_response(user_input)

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
    app.run(debug=True, port=5000)