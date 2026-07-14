(function () {
    var shell = document.getElementById("app-shell");
    var sidebar = document.getElementById("app-sidebar");
    var appMain = document.getElementById("app-main");
    var menuToggle = document.querySelector("[data-app-menu-toggle]");
    var menuCloseButtons = document.querySelectorAll("[data-app-menu-close]");
    var themeToggle = document.querySelector("[data-app-theme-toggle]");
    var commandOpen = document.querySelector("[data-command-open]");
    var commandPalette = document.querySelector("[data-command-palette]");
    var commandInput = document.querySelector("[data-command-input]");
    var commandItems = Array.prototype.slice.call(
        document.querySelectorAll("[data-command-item]")
    );
    var commandCloseButtons = document.querySelectorAll("[data-command-close]");
    var commandEmpty = document.querySelector("[data-command-empty]");
    var commandPreviousFocus = null;
    var mobileMenuQuery = window.matchMedia("(max-width: 767px)");

    if (!shell) {
        return;
    }

    function menuFocusableElements() {
        if (!sidebar) {
            return [];
        }
        return Array.prototype.slice.call(
            sidebar.querySelectorAll(
                'a[href], button:not([disabled]):not([tabindex="-1"])'
            )
        );
    }

    function setElementInert(element, isInert) {
        if (!element) {
            return;
        }
        element.inert = isInert;
        if (isInert) {
            element.setAttribute("inert", "");
        } else {
            element.removeAttribute("inert");
        }
    }

    function syncMenuAccessibility() {
        var isMobile = mobileMenuQuery.matches;
        var isOpen = isMobile && shell.classList.contains("is-menu-open");
        setElementInert(sidebar, isMobile && !isOpen);
        setElementInert(appMain, isOpen);
        if (sidebar) {
            if (isMobile && !isOpen) {
                sidebar.setAttribute("aria-hidden", "true");
            } else {
                sidebar.removeAttribute("aria-hidden");
            }
        }
    }

    function setMenuOpen(isOpen, focusMenu) {
        isOpen = Boolean(isOpen && mobileMenuQuery.matches);
        shell.classList.toggle("is-menu-open", isOpen);
        document.body.classList.toggle("is-app-menu-open", isOpen);
        if (menuToggle) {
            menuToggle.setAttribute("aria-expanded", String(isOpen));
        }
        syncMenuAccessibility();
        if (isOpen && focusMenu !== false) {
            window.requestAnimationFrame(function () {
                var closeButton = sidebar && sidebar.querySelector("[data-app-menu-close]");
                var focusable = menuFocusableElements();
                var initialFocus = closeButton || focusable[0];
                if (initialFocus) {
                    initialFocus.focus();
                }
            });
        }
    }

    function closeMenu(restoreFocus) {
        setMenuOpen(false, false);
        if (restoreFocus !== false && menuToggle) {
            menuToggle.focus();
        }
    }

    function trapMenuFocus(event) {
        if (
            event.key !== "Tab" ||
            !mobileMenuQuery.matches ||
            !shell.classList.contains("is-menu-open")
        ) {
            return;
        }
        var focusable = menuFocusableElements();
        if (!focusable.length) {
            return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    if (menuToggle) {
        menuToggle.addEventListener("click", function () {
            setMenuOpen(!shell.classList.contains("is-menu-open"));
        });
    }

    menuCloseButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            closeMenu(true);
        });
    });

    function handleMenuBreakpointChange(event) {
        var focusedInSidebar = Boolean(
            sidebar && sidebar.contains(document.activeElement)
        );
        var wasOpen = shell.classList.contains("is-menu-open");
        setMenuOpen(false, false);
        if (event.matches && focusedInSidebar && menuToggle) {
            menuToggle.focus();
        } else if (!event.matches && wasOpen && sidebar) {
            var currentLink = sidebar.querySelector('a[aria-current="page"]');
            var focusable = menuFocusableElements();
            var nextFocus = currentLink || focusable[0];
            if (nextFocus) {
                nextFocus.focus();
            }
        }
    }

    if (typeof mobileMenuQuery.addEventListener === "function") {
        mobileMenuQuery.addEventListener("change", handleMenuBreakpointChange);
    } else {
        mobileMenuQuery.addListener(handleMenuBreakpointChange);
    }
    syncMenuAccessibility();

    function visibleCommandItems() {
        return commandItems.filter(function (item) {
            return !item.hidden;
        });
    }

    function clearCommandSelection() {
        commandItems.forEach(function (item) {
            item.classList.remove("is-selected");
        });
    }

    function selectCommandItem(item, moveFocus) {
        if (!item) {
            return;
        }
        clearCommandSelection();
        item.classList.add("is-selected");
        if (moveFocus) {
            item.focus();
        }
    }

    function filterCommandItems() {
        var query = commandInput ? commandInput.value.trim().toLowerCase() : "";
        var visible = [];
        commandItems.forEach(function (item) {
            var label = (item.getAttribute("data-command-label") || "").toLowerCase();
            item.hidden = Boolean(query && label.indexOf(query) === -1);
            if (!item.hidden) {
                visible.push(item);
            }
        });
        clearCommandSelection();
        if (commandEmpty) {
            commandEmpty.hidden = visible.length !== 0;
        }
        return visible;
    }

    function openCommandPalette() {
        if (!commandPalette || !commandInput) {
            return;
        }
        commandPreviousFocus = document.activeElement;
        if (
            !commandPreviousFocus ||
            commandPreviousFocus === document.body ||
            commandPreviousFocus === document.documentElement
        ) {
            commandPreviousFocus = commandOpen;
        }
        setMenuOpen(false, false);
        commandPalette.hidden = false;
        document.body.classList.add("is-command-palette-open");
        if (commandOpen) {
            commandOpen.setAttribute("aria-expanded", "true");
        }
        commandInput.value = "";
        filterCommandItems();
        window.requestAnimationFrame(function () {
            commandInput.focus();
        });
    }

    function closeCommandPalette(restoreFocus) {
        if (!commandPalette || commandPalette.hidden) {
            return;
        }
        commandPalette.hidden = true;
        document.body.classList.remove("is-command-palette-open");
        if (commandOpen) {
            commandOpen.setAttribute("aria-expanded", "false");
        }
        clearCommandSelection();
        if (restoreFocus !== false && commandPreviousFocus) {
            commandPreviousFocus.focus();
        }
    }

    function moveCommandSelection(direction) {
        var visible = visibleCommandItems();
        if (!visible.length) {
            return;
        }
        var currentIndex = visible.indexOf(document.activeElement);
        var nextIndex;
        if (currentIndex === -1) {
            nextIndex = direction > 0 ? 0 : visible.length - 1;
        } else {
            nextIndex = (currentIndex + direction + visible.length) % visible.length;
        }
        selectCommandItem(visible[nextIndex], true);
    }

    function trapCommandFocus(event) {
        if (!commandPalette || commandPalette.hidden || event.key !== "Tab") {
            return;
        }
        var focusable = Array.prototype.slice.call(
            commandPalette.querySelectorAll(
                'input:not([disabled]), button:not([disabled]):not([tabindex="-1"]), a:not([hidden])'
            )
        );
        if (!focusable.length) {
            return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    if (commandOpen) {
        commandOpen.addEventListener("click", openCommandPalette);
    }

    commandCloseButtons.forEach(function (button) {
        button.addEventListener("click", function () {
            closeCommandPalette(true);
        });
    });

    if (commandInput) {
        commandInput.addEventListener("input", filterCommandItems);
        commandInput.addEventListener("keydown", function (event) {
            if (event.key === "ArrowDown") {
                event.preventDefault();
                moveCommandSelection(1);
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                moveCommandSelection(-1);
            } else if (event.key === "Enter") {
                var visible = visibleCommandItems();
                if (visible.length) {
                    event.preventDefault();
                    visible[0].click();
                }
            }
        });
    }

    commandItems.forEach(function (item) {
        item.addEventListener("focus", function () {
            selectCommandItem(item, false);
        });
        item.addEventListener("keydown", function (event) {
            if (event.key === "ArrowDown") {
                event.preventDefault();
                moveCommandSelection(1);
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                moveCommandSelection(-1);
            }
        });
        item.addEventListener("click", function () {
            closeCommandPalette(false);
        });
    });

    document.addEventListener("keydown", function (event) {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
            event.preventDefault();
            if (commandPalette && !commandPalette.hidden) {
                closeCommandPalette(true);
            } else {
                openCommandPalette();
            }
            return;
        }
        if (commandPalette && !commandPalette.hidden) {
            if (event.key === "Escape") {
                event.preventDefault();
                closeCommandPalette(true);
                return;
            }
            trapCommandFocus(event);
        }
        trapMenuFocus(event);
        if (event.key === "Escape" && shell.classList.contains("is-menu-open")) {
            closeMenu(true);
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
