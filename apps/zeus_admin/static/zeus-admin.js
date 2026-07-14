(function () {
    var shell = document.querySelector("[data-zeus-admin-shell]");
    var sidebar = document.getElementById("zeus-admin-sidebar");
    var main = document.getElementById("zeus-admin-main");
    var toggle = document.querySelector("[data-admin-menu-toggle]");
    var closeButtons = document.querySelectorAll("[data-admin-menu-close]");
    var mobileQuery = window.matchMedia("(max-width: 767px)");

    if (!shell || !sidebar) {
        return;
    }

    function setInert(element, value) {
        if (!element) {
            return;
        }
        element.inert = value;
        if (value) {
            element.setAttribute("inert", "");
        } else {
            element.removeAttribute("inert");
        }
    }

    function focusableItems() {
        return Array.prototype.slice.call(
            sidebar.querySelectorAll('a[href], button:not([disabled]):not([tabindex="-1"])')
        );
    }

    function syncAccessibility() {
        var open = mobileQuery.matches && shell.classList.contains("is-menu-open");
        setInert(sidebar, mobileQuery.matches && !open);
        setInert(main, open);
        if (mobileQuery.matches && !open) {
            sidebar.setAttribute("aria-hidden", "true");
        } else {
            sidebar.removeAttribute("aria-hidden");
        }
    }

    function setOpen(open, restoreFocus) {
        open = Boolean(open && mobileQuery.matches);
        shell.classList.toggle("is-menu-open", open);
        document.body.classList.toggle("is-zeus-admin-menu-open", open);
        if (toggle) {
            toggle.setAttribute("aria-expanded", String(open));
        }
        syncAccessibility();
        if (open) {
            window.requestAnimationFrame(function () {
                var close = sidebar.querySelector("[data-admin-menu-close]");
                (close || focusableItems()[0]).focus();
            });
        } else if (restoreFocus && toggle) {
            toggle.focus();
        }
    }

    if (toggle) {
        toggle.addEventListener("click", function () {
            setOpen(!shell.classList.contains("is-menu-open"), false);
        });
    }

    closeButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            setOpen(false, true);
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && shell.classList.contains("is-menu-open")) {
            setOpen(false, true);
            return;
        }
        if (event.key !== "Tab" || !shell.classList.contains("is-menu-open")) {
            return;
        }
        var items = focusableItems();
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    function handleBreakpoint() {
        var focusedInSidebar = sidebar.contains(document.activeElement);
        setOpen(false, false);
        if (mobileQuery.matches && focusedInSidebar && toggle) {
            toggle.focus();
        }
    }

    if (typeof mobileQuery.addEventListener === "function") {
        mobileQuery.addEventListener("change", handleBreakpoint);
    } else {
        mobileQuery.addListener(handleBreakpoint);
    }
    syncAccessibility();
})();
