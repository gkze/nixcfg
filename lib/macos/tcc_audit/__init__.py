"""TCC (Transparency, Consent, and Control) privacy-list auditing.

Read-only classification of every macOS privacy-list entry, plus typed
helpers for pane-driven removal. Removal itself always goes through the
real System Settings pane and requires the user's own authentication.
"""

from lib.macos.tcc_audit.model import Auth, Bundle, ClientKind, Pane, Service, TCCEntry

__all__ = ["Auth", "Bundle", "ClientKind", "Pane", "Service", "TCCEntry"]
