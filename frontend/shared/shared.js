/*
 * shared/shared.js
 * Renders the global header + footer into every normal page, and drives
 * the theme system (dark/light, persisted via localStorage — theme
 * preference ONLY, never authentication state).
 *
 * Auth/session logic here talks to the same backend endpoints script.js
 * and auth.js already used (/api/auth/me, /api/auth/logout) with
 * credentials:"include" against the real HttpOnly-cookie session. This
 * file does not introduce a new auth mechanism — it centralizes the
 * "who is logged in, show it in the header" display logic that used to
 * live only in script.js's window.onload, because the header (and the
 * account area) is now shared across every page instead of appearing
 * only on index.html.
 */

(function () {
  "use strict";

  var AUTH_ME_URL = "https://policy-tracker-b8a3.onrender.com/api/auth/me";
  var LOGOUT_URL = "https://policy-tracker-b8a3.onrender.com/api/auth/logout";
  var THEME_KEY = "theme";

  /* ------------------------------------------------------------------
     THEME
     ------------------------------------------------------------------ */

  var Theme = {
    get: function () {
      try {
        return localStorage.getItem(THEME_KEY);
      } catch (e) {
        return null;
      }
    },
    set: function (value) {
      try {
        localStorage.setItem(THEME_KEY, value);
      } catch (e) {
        /* localStorage unavailable (private mode etc.) - theme just
           won't persist across pages, nothing else breaks */
      }
    },
    apply: function (value) {
      document.documentElement.setAttribute("data-theme", value);
    },
    current: function () {
      return document.documentElement.getAttribute("data-theme") === "light"
        ? "light"
        : "dark";
    },
    init: function () {
      // The <head> of every page also runs a tiny inline snippet before
      // this file loads, so the correct theme is already applied and
      // there's no flash of the wrong theme. This just makes sure it's
      // consistent and wires the toggle button.
      var stored = Theme.get();
      if (stored === "light" || stored === "dark") {
        Theme.apply(stored);
      } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
        Theme.apply("light");
      } else {
        Theme.apply("dark");
      }
    },
    toggle: function () {
      var next = Theme.current() === "dark" ? "light" : "dark";
      Theme.apply(next);
      Theme.set(next);
    },
  };

  /* ------------------------------------------------------------------
     ICONS (inline SVG, no external icon library)
     ------------------------------------------------------------------ */

  var ICONS = {
    sun:
      '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"></path></svg>',
    moon:
      '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>',
    menu:
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"></path></svg>',
  };

  /* ------------------------------------------------------------------
     HEADER
     ------------------------------------------------------------------ */

  var NAV_ITEMS = [
    { key: "home", label: "Home", href: "index.html" },
    { key: "policies", label: "Policies", href: "policies.html" },
    { key: "references", label: "References", href: "reference.html" },
    { key: "contact", label: "Contact", href: "contact.html" },
  ];

  function renderHeader(mode) {
    var mount = document.getElementById("app-header");
    if (!mount) return;

    var activeKey = document.body.getAttribute("data-page") || "";
    var isSimple = mode === "simple";

    var navHtml = NAV_ITEMS.map(function (item) {
      var activeClass = item.key === activeKey ? " is-active" : "";
      return (
        '<a href="' + item.href + '" class="' + activeClass.trim() + '">' +
        item.label +
        "</a>"
      );
    }).join("");

    var html =
      '<div class="app-header' + (isSimple ? " app-header--simple" : "") + '">' +
        '<a href="index.html" class="app-header__logo">Policy Tracker</a>' +
        (isSimple ? "" :
          '<button type="button" class="app-header__menu-btn" id="header-menu-btn" aria-label="Toggle navigation" aria-expanded="false">' +
            ICONS.menu +
          "</button>" +
          '<nav class="app-header__nav" id="header-nav">' + navHtml + "</nav>"
        ) +
        '<div class="app-header__right">' +
          '<button type="button" class="theme-toggle" id="theme-toggle-btn" aria-label="Toggle dark and light mode">' +
            ICONS.sun + ICONS.moon +
          "</button>" +
          (isSimple
            ? '<a href="index.html" class="btn btn-ghost">Back to Home</a>'
            : '<div class="app-header__account" id="header-account"><a href="login.html" class="btn btn-secondary" id="header-signin-link">Sign In</a></div>'
          ) +
        "</div>" +
      "</div>";

    mount.innerHTML = html;

    var themeBtn = document.getElementById("theme-toggle-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", Theme.toggle);
    }

    if (!isSimple) {
      var menuBtn = document.getElementById("header-menu-btn");
      var nav = document.getElementById("header-nav");
      if (menuBtn && nav) {
        menuBtn.addEventListener("click", function () {
          var isOpen = nav.classList.toggle("is-open");
          menuBtn.setAttribute("aria-expanded", String(isOpen));
        });
      }
    }
  }

  /* ------------------------------------------------------------------
     FOOTER
     ------------------------------------------------------------------ */

  function renderFooter(mode) {
    var mount = document.getElementById("app-footer");
    if (!mount) return;

    var isSimple = mode === "simple";
    var year = new Date().getFullYear();

    var fullHtml =
      '<div class="app-footer__inner">' +
        '<div class="app-footer__brand">' +
          "<h4>Policy Tracker</h4>" +
          "<p>Track and explore public policy changes across sectors using a policy database and an AI-assisted chatbot.</p>" +
        "</div>" +
        '<div class="app-footer__col">' +
          "<h5>Navigate</h5>" +
          "<ul>" +
            '<li><a href="index.html">Home</a></li>' +
            '<li><a href="policies.html">Policies</a></li>' +
            '<li><a href="reference.html">References</a></li>' +
            '<li><a href="contact.html">Contact</a></li>' +
          "</ul>" +
        "</div>" +
        '<div class="app-footer__col">' +
          "<h5>Legal</h5>" +
          "<ul>" +
            '<li><a href="terms.html">Terms &amp; Conditions</a></li>' +
            '<li><a href="privacy.html">Privacy Policy</a></li>' +
          "</ul>" +
        "</div>" +
      "</div>";

    var disclaimerHtml =
      '<details class="app-footer__disclaimer">' +
        "<summary>Disclaimer</summary>" +
        '<div class="app-footer__disclaimer-text">' +
          "This project has been developed solely for academic and educational purposes. The information presented is based on publicly available data and is intended to demonstrate the use of artificial intelligence and web technologies for analyzing and presenting public policy information.\n\n" +
          "This platform does not aim to harm, defame, or misrepresent any individual, organization, political party, or government. Any opinions or interpretations generated by the AI component are automated responses and should not be considered authoritative or official statements.\n\n" +
          "Users are advised to verify information from official government sources before making any decisions. The developers of this project are not responsible for any misuse, misinterpretation, or consequences arising from the use of this application." +
        "</div>" +
      "</details>";

    var bottomHtml =
      '<div class="app-footer__bottom">' +
        '<div class="app-footer__bottom-inner">' +
          '<p class="app-footer__copyright">© ' + year + ' Policy Tracker. All rights reserved.</p>' +
          disclaimerHtml +
        "</div>" +
      "</div>";

    mount.innerHTML =
      '<footer class="app-footer' + (isSimple ? " app-footer--simple" : "") + '">' +
        (isSimple ? "" : fullHtml) +
        bottomHtml +
      "</footer>";
  }

  /* ------------------------------------------------------------------
     AUTH DISPLAY (guest vs signed-in account area in the header)
     ------------------------------------------------------------------ */

  function renderAccountArea(user) {
    var slot = document.getElementById("header-account");
    if (!slot) return;

    if (user && user.name) {
      slot.innerHTML =
        '<span class="app-header__username">' + escapeHtml(user.name) + "</span>" +
        '<button type="button" class="btn btn-danger" id="header-logout-btn">Logout</button>';
      var logoutBtn = document.getElementById("header-logout-btn");
      if (logoutBtn) {
        logoutBtn.addEventListener("click", logout);
      }
    }
    // If no user, the default "Sign In" link rendered in renderHeader() stays as-is.
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function initAuthDisplay() {
    fetch(AUTH_ME_URL, { credentials: "include" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (data) {
        var user = data && data.user ? data.user : null;
        renderAccountArea(user);
        // Kept for any page-specific script (e.g. script.js on index.html)
        // that still reads a #username element directly.
        var legacyUsername = document.getElementById("username");
        if (legacyUsername) {
          legacyUsername.textContent = user ? user.name : "Guest";
        }
      })
      .catch(function () {
        var legacyUsername = document.getElementById("username");
        if (legacyUsername) {
          legacyUsername.textContent = "Guest";
        }
      });
  }

  function logout() {
    fetch(LOGOUT_URL, {
      method: "POST",
      credentials: "include",
    }).finally(function () {
      window.location.href = "login.html";
    });
  }

  /* ------------------------------------------------------------------
     PUBLIC API + AUTO INIT
     ------------------------------------------------------------------ */

  window.PolicyTrackerShell = {
    initTheme: Theme.init,
    toggleTheme: Theme.toggle,
    renderHeader: renderHeader,
    renderFooter: renderFooter,
    initAuthDisplay: initAuthDisplay,
    logout: logout,
  };

  // Kept as a global for any inline handler/legacy script expecting it.
  window.logout = logout;

  document.addEventListener("DOMContentLoaded", function () {
    var mode = document.body.getAttribute("data-header") === "simple" ? "simple" : "full";
    Theme.init();
    renderHeader(mode);
    renderFooter(mode);
    if (mode === "full") {
      initAuthDisplay();
    }
  });
})();
