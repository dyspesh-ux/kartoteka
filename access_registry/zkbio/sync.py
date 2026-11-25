"""Sync of a ZKBio CVAccess server (СКУД): personnel mirror and access events.

Read-only: nothing is written to the ACS database. Every run writes a Sync Log
(kind «СКУД») and runs inside one savepoint.
"""

from frappe.utils import cint


def pass_nos(server) -> set[int]:
	"""Event codes treated as passages; empty config means the default {0}."""
	raw = server.get("pass_event_nos") or ""
	codes = {cint(part) for part in str(raw).split(",") if part.strip().isdigit()}
	return codes or {0}
