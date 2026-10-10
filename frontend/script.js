const API_URL = "https://policy-tracker-b8a3.onrender.com/chat";
const API_ROOT = API_URL.replace(/\/chat$/, "");
let isLoading = false;

// Phase 15: set only when the page is opened as index.html?conversation=<id>
// (the dashboard's "Continue" link). While null, /chat is called exactly as
// before - anonymous, no conversation_id, nothing persisted.
let activeConversationId = null;

// Run after page loads
// NOTE (UI shell phase): the "who is logged in" fetch to /api/auth/me and
// the logout() call used to live here. They have moved to shared/shared.js
// because the account area (username + logout) is now part of the global
// header shown on every page, not just index.html. The fetch itself,
// endpoint, and credentials:"include" behavior are unchanged - only where
// the code lives changed. Chat behavior below is untouched.
window.onload = function () {
    let input = document.getElementById("userInput");
    if (input) {
        input.addEventListener("keypress", function(e) {
            if (e.key === "Enter") {
                sendMessage();
            }
        });
    }

    // Minimum integration for policies.js's "Ask AI About This Policy"
    // button: it navigates here with ?ask=<question>, which we populate
    // into the existing chatbot input and send through the existing
    // sendMessage()/chat pipeline - no second chatbot, no chatbot.py or
    // retrieval.py changes.
    const conversationParam = new URLSearchParams(window.location.search).get("conversation");
    if (conversationParam && /^[1-9][0-9]{0,9}$/.test(conversationParam)) {
        activeConversationId = Number(conversationParam);
        loadConversationHistory(activeConversationId);
    }

    const askParam = new URLSearchParams(window.location.search).get("ask");
    if (askParam && input) {
        input.value = askParam;
        sendMessage();
        // Clean the URL so refreshing the page doesn't resend the same question.
        history.replaceState(null, "", window.location.pathname);
    }
};

// Send message
async function sendMessage() {
    if (isLoading) return;

    let inputField = document.getElementById("userInput");
    let userInput = inputField.value.trim();

    if (userInput === "") return;

    isLoading = true;

    addMessage(userInput, "user");
    inputField.value = "";

    let loadingMsg = addMessage("...", "bot");

    try {
        const requestOptions = {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ message: userInput })
        };
        if (activeConversationId !== null) {
            // Continuing a saved conversation: send the HttpOnly session
            // cookie and the id so the backend verifies ownership and
            // persists both messages (see backend/app.py /chat).
            requestOptions.credentials = "include";
            requestOptions.body = JSON.stringify({
                message: userInput,
                conversation_id: activeConversationId
            });
        }

        let response = await fetch(API_URL, requestOptions);

        let data = await response.json();

        loadingMsg.remove();

        if (data.reply) {
            addMessage(data.reply, "bot");
        } else if (activeConversationId !== null && response.status === 401) {
            addMessage("Please sign in again to continue this conversation.", "bot");
        } else if (activeConversationId !== null && response.status === 404) {
            addMessage("That conversation could not be found.", "bot");
        } else {
            addMessage("No response from server.", "bot");
        }

    } catch (error) {
        loadingMsg.remove();
        addMessage("Server error. Is backend running?", "bot");
    }

    isLoading = false;
}

// Phase 15: replay a saved conversation into the existing chatbox so the
// user can continue it. Uses the existing Phase 12 endpoint; message text
// goes through addMessage() (innerText), never innerHTML.
async function loadConversationHistory(conversationId) {
    const base = `${API_ROOT}/api/conversations/${conversationId}/messages`;
    const loadingMsg = addMessage("Loading conversation...", "bot");

    async function fetchPage(page) {
        const response = await fetch(`${base}?per_page=200&page=${page}`, {
            credentials: "include"
        });
        if (!response.ok) {
            const err = new Error("history");
            err.status = response.status;
            throw err;
        }
        return response.json();
    }

    try {
        let result = await fetchPage(1);
        const pages = result && result.pagination ? result.pagination.pages : 1;
        // Messages are oldest-first; for a long conversation show the most
        // recent page so the user continues where they left off.
        if (pages > 1) {
            result = await fetchPage(pages);
        }
        loadingMsg.remove();

        const items = Array.isArray(result && result.data) ? result.data : [];
        if (pages > 1) {
            addMessage("Showing the most recent messages from this conversation.", "bot");
        }
        items.forEach(function (item) {
            if (!item || typeof item.content !== "string") return;
            if (item.role === "user") addMessage(item.content, "user");
            else if (item.role === "assistant") addMessage(item.content, "bot");
        });
    } catch (error) {
        loadingMsg.remove();
        if (error && error.status === 401) {
            window.location.replace("login.html");
            return;
        }
        if (error && error.status === 404) {
            activeConversationId = null; // fall back to a normal, unsaved chat
            addMessage("That conversation could not be found. You can start a new question below.", "bot");
        } else {
            addMessage("Unable to load this conversation right now.", "bot");
        }
    }
}

// Add message
function addMessage(text, sender) {
    let chatbox = document.getElementById("chatbox");

    let msg = document.createElement("div");
    msg.classList.add("message", sender);
    msg.innerText = text;

    chatbox.appendChild(msg);
    chatbox.scrollTop = chatbox.scrollHeight;

    return msg;
}

// Quick category
function sendCategory(category) {
    let inputField = document.getElementById("userInput");
    inputField.value = "Show policies in " + category;
    sendMessage();
}

// Focus input
function focusInput() {
    document.getElementById("userInput").focus();
}
