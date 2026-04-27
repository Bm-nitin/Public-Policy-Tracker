function toggle() {
document.getElementById("container").classList.toggle("active");
}

/* SIGNUP */
function signup() {
let name = document.getElementById("signupName").value;
let email = document.getElementById("signupEmail").value;
let pass = document.getElementById("signupPassword").value;

if (!name || !email || !pass) {
    alert("All fields are required");
    return;
}

if (!email.includes("@") || !email.includes(".")) {
    alert("Invalid email or password (min 6 chars)");
    return;
}

let user = {
    name: name,
    password: pass
};

localStorage.setItem(email, JSON.stringify(user));

alert("Signup successful! Now login.");
toggle();


}

/* LOGIN */
function login() {
let email = document.getElementById("loginEmail").value;
let pass = document.getElementById("loginPassword").value;


let user = JSON.parse(localStorage.getItem(email));

if (!user) {
    alert("User not found");
    return;
}

if (user.password === pass) {
    localStorage.setItem("loggedInUser", email);
    window.location.href = "index.html";
} else {
    alert("Wrong password");
}


}

