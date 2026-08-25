const API_URL = "https://policy-tracker-b8a3.onrender.com/chat";
let isLoading = false;

// Run after page loads
window.onload = function () {
    let user = localStorage.getItem("loggedInUser");
    document.getElementById("username").innerText = user || "Guest";

    let input = document.getElementById("userInput");
    if (input) {
        input.addEventListener("keypress", function(e) {
            if (e.key === "Enter") {
                sendMessage();
            }
        });
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
        let response = await fetch(API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ message: userInput })
        });

        let data = await response.json();

        loadingMsg.remove();

        if (data.reply) {
            addMessage(data.reply, "bot");
        } else {
            addMessage("No response from server.", "bot");
        }

    } catch (error) {
        loadingMsg.remove();
        addMessage("Server error. Is backend running?", "bot");
    }

    isLoading = false;
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

// Navigation
function goToContact() {
    window.location.href = "contact.html";
}

function goToReferences() {
    window.location.href = "reference.html";
}

function logout() {
    localStorage.removeItem("loggedInUser");
    window.location.href = "login.html";
}

function goHome() {
    window.location.href = "index.html";
}
