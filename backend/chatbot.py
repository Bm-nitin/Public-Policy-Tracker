from utils import clean_text, similarity_score, match_by_keywords
from policy_loader import load_policies
import google.generativeai as genai
from dotenv import load_dotenv
import os
import random
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(dotenv_path=ENV_PATH)

API_KEY = os.getenv("API_KEY")


genai.configure(api_key=API_KEY)

policies = load_policies()

CATEGORY_KEYWORDS = {
    "education": ["education", "school", "college", "student"],
    "healthcare": ["health", "hospital", "medical"],
    "technology": ["technology", "ai", "digital", "cyber"],
    "environment": ["environment", "climate", "pollution"],
    "economy": ["economy", "finance", "bank", "tax"],
    "agriculture": ["agriculture", "farmer", "crop"]
}


def detect_category(user_input):
    text = clean_text(user_input)
    words = text.split()

    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for word in keywords if word in words)

    if max(scores.values()) == 0:
        return None

    best = max(scores, key=scores.get)

    # ✅ SMART LOGIC
    # If user directly mentions category → accept
    if best in words:
        return best

    # Otherwise require stronger match
    return best if scores[best] >= 1 else None

def clean_ai_response(text):
    text = re.sub(r':contentReference\[.*?\]', '', text)
    return text.strip()

# 🤖 Gemini fallback
def call_generative_ai(user_input):
    try:
        print(f"[AI REQUEST] {user_input}")

        model = genai.GenerativeModel("gemini-flash-latest")
        response = model.generate_content(user_input)

        raw = getattr(response, "text", None)

        # print(f"[AI RESPONSE] {raw}")

        if not raw:
            return "No response from AI."

        return clean_ai_response(raw)

    except Exception as e:
        print("REAL ERROR:", repr(e))
        return f"AI service error: {str(e)}"
  
def is_policy_related(user_input):
    text = clean_text(user_input)

    allowed_keywords = [
        "policy", "policies", "scheme", "government",
        "agriculture", "health", "education",
        "technology", "economy", "environment",
        "farmer", "tax", "bank", "insurance"
    ]

    return any(word in text for word in allowed_keywords)

def get_response(user_input):

    user_input_clean = clean_text(user_input)

    # 1. CATEGORY FIRST
    category = detect_category(user_input)

    if category:
        related = [
            p for p in policies
            if any(
                word in clean_text(p.get("name", "")).split()
                for word in CATEGORY_KEYWORDS.get(category, [])
            )
        ]

        if related:
            return format_multiple(related[:3], category)

        

    # 2. similarity match
    best_match = None
    best_score = 0

    for policy in policies:
        name_clean = clean_text(policy.get("name", ""))
        score = similarity_score(user_input_clean, name_clean)

        if score > best_score:
            best_score = score
            best_match = policy

    if best_match and best_score > 0.8:
        return format_response(best_match)

    # 3. keyword match
    kw_match, kw_score = match_by_keywords(user_input, policies)

    if kw_match and kw_score > 0.5:
        return format_response(kw_match)

    # 4. AI fallback (NOW IT WILL RUN)
    return call_generative_ai(user_input)


def format_response(policy):
    return (
        f"Policy: {policy.get('name')}\n\n"
        f"Change: {policy.get('change')}\n\n"
        f"Impact: {policy.get('impact')}"
    )


def format_multiple(policies, category):
    response = f"Showing {category} policies:\n\n"

    for p in policies:
        response += f"• {p['name']}\n"
        response += f"  → {p['impact']}\n\n"

    return response.strip()