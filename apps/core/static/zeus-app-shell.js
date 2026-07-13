(function () {
    var shell = document.getElementById("app-shell");
    var menuToggle = document.querySelector("[data-app-menu-toggle]");
    var menuCloseButtons = document.querySelectorAll("[data-app-menu-close]");
    var themeToggle = document.querySelector("[data-app-theme-toggle]");

    if (!shell) {
        return;
    }

    function setMenuOpen(isOpen) {
        shell.classList.toggle("is-menu-open", isOpen);
        document.body.classList.toggle("is-app-menu-open", isOpen);
        if (menuToggle) {
            menuToggle.setAttribute("aria-expanded", String(isOpen));
        }
    }

    if (menuToggle) {
        menuToggle.addEventListener("click", function () {
            setMenuOpen(!shell.classList.contains("is-menu-open"));
        });
    }

    menuCloseButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            setMenuOpen(false);
            if (menuToggle) {
                menuToggle.focus();
            }
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && shell.classList.contains("is-menu-open")) {
            setMenuOpen(false);
            if (menuToggle) {
                menuToggle.focus();
            }
        }
    });

    function syncThemeControl() {
        if (!themeToggle) {
            return;
        }
        var isDark = document.documentElement.classList.contains("dark");
        var themeIcon = themeToggle.querySelector("span");
        themeToggle.setAttribute(
            "aria-label",
            isDark ? "Attiva tema chiaro" : "Attiva tema scuro"
        );
        if (themeIcon) {
            themeIcon.textContent = isDark ? "☀" : "☾";
        }
    }

    if (themeToggle) {
        themeToggle.addEventListener("click", function () {
            var isDark = document.documentElement.classList.toggle("dark");
            try {
                localStorage.setItem("zeus-theme", isDark ? "dark" : "light");
            } catch (error) {
                void error;
            }
            syncThemeControl();
        });
        syncThemeControl();
    }
})();