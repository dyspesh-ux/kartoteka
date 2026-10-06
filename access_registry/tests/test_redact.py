"""Secrets never reach the logs: Bitrix24 webhook URLs, tokens, passwords."""

from frappe.tests.utils import FrappeTestCase

from access_registry.bitrix24.client import B24Client, B24Error
from access_registry.redact import redact
from access_registry.sync.engine import _format_messages

SECRET = "k3x9q2wz8m1n4p7r"


class FailingSession:
	def post(self, url, **kw):
		import requests

		raise requests.exceptions.ConnectionError(
			f"HTTPSConnectionPool(host='b24.example.local', port=443): Max retries exceeded with url: {url}"
		)


class TestRedact(FrappeTestCase):
	def test_patterns(self):
		self.assertNotIn(SECRET, redact(f"https://b24.example.local/rest/1/{SECRET}/user.get.json"))
		self.assertIn("/rest/1/***", redact(f"url: /rest/1/{SECRET}/user.get.json"))
		self.assertNotIn("p@ss", redact("GET https://x/api?token=abc123&password=p@ss&page=2"))
		self.assertIn("page=2", redact("GET https://x/api?token=abc123&page=2"))
		self.assertNotIn("hunter2", redact("ldaps://svc:hunter2@dc1.corp.local"))
		self.assertNotIn("eyJhbGciOi", redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc"))
		self.assertEqual(redact("обычный текст: 15 пользователей"), "обычный текст: 15 пользователей")
		self.assertNotIn(SECRET, _format_messages([f"ошибка /rest/7/{SECRET}/x"]))

	def test_network_error_has_no_secret(self):
		client = B24Client(
			f"https://b24.example.local/rest/1/{SECRET}/",
			session=FailingSession(),
			retries=1,
			sleep=lambda s: None,
		)
		with self.assertRaises(B24Error) as caught:
			client.call("user.get")
		self.assertNotIn(SECRET, str(caught.exception))
		self.assertIsNone(caught.exception.__cause__)  # no chained exception with the URL in tracebacks
		self.assertTrue(caught.exception.__suppress_context__)
