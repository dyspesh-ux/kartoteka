"""Secrets out of texts that are stored or shown: sync logs, error messages, notifications.

A Bitrix24 inbound webhook carries its secret in the URL path (…/rest/1/<secret>/user.get.json), and
network errors of ``requests`` quote that URL. Tokens and passwords in query strings and in
«user:password@host» are hidden too.
"""

import re

PATTERNS = (
	# Bitrix24 webhook: /rest/<user id>/<secret>/
	(re.compile(r"(/rest/\d+/)[A-Za-z0-9_\-]{6,}"), r"\1***"),
	# ?token=…, &password=…, auth=…, api_key=…
	(
		re.compile(r"(?i)\b(token|password|passwd|pwd|secret|auth|api[_-]?key|access_token)=([^&\s'\"]+)"),
		r"\1=***",
	),
	# scheme://user:password@host
	(re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):[^@\s/]+@"), r"\1:***@"),
	# Authorization: Bearer …
	(re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"), r"\1***"),
)


def redact(text):
	if not text:
		return text
	text = str(text)
	for pattern, replacement in PATTERNS:
		text = pattern.sub(replacement, text)
	return text
