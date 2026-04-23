// Managed by nixcfg nixcfg.zen.
// Enables userChrome.css / userContent.css loading for Zen/Twilight.
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
// Force chrome-side prefers-color-scheme to evaluate as dark so Catppuccin
// userChrome rules (all gated on @media (prefers-color-scheme: dark)) apply.
// Workaround for zen-browser/desktop#9542; see also #9955 / #9229.
user_pref("ui.systemUsesDarkTheme", 1);
