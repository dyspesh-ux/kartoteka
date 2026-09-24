#!/usr/bin/env python3
"""A local stand-in for the HR_Export_API HTTP service of 1C:ZUP.

Serves <dir>/{meta,organizations,departments,employees,absences}.json with basic auth,
so the whole chain (HR Source → HTTP client → background job → Sync Log) can be tried
without a real ZUP. Only synthetic data: never put real exports here.

    python3 tools/mock_hr_export.py --dir access_registry/tests/fixtures/zup1 --port 8765 \
        --user svc_hr_export --password secret

HR Source: base URL http://127.0.0.1:8765/hs/hr_export, the same user and password.
"""

import argparse
import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ENDPOINTS = ("meta", "organizations", "departments", "employees", "absences")


def make_handler(directory, user, password, prefix):
	expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

	class Handler(BaseHTTPRequestHandler):
		def do_GET(self):
			if user and self.headers.get("Authorization") != expected:
				self.send_response(401)
				self.send_header("WWW-Authenticate", 'Basic realm="hr_export"')
				self.end_headers()
				return
			url = urlparse(self.path)
			path = url.path
			if prefix and path.startswith(prefix):
				path = path[len(prefix) :]
			endpoint = path.strip("/")
			if endpoint not in ENDPOINTS:
				self.send_response(404)
				self.end_headers()
				return
			with open(os.path.join(directory, f"{endpoint}.json"), encoding="utf-8") as fh:
				data = json.load(fh)
			if endpoint == "absences":
				params = parse_qs(url.query)
				date_from = (params.get("from") or [""])[0]
				date_to = (params.get("to") or [""])[0]
				if date_from and date_to:
					data = [
						r
						for r in data
						if (r.get("ДатаОкончания") or "9999-12-31") >= date_from
						and r.get("ДатаНачала", "") <= date_to
					]
			body = json.dumps(data, ensure_ascii=False).encode("utf-8")
			self.send_response(200)
			self.send_header("Content-Type", "application/json; charset=utf-8")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)

		def log_message(self, fmt, *args):
			print("mock_hr_export:", fmt % args)

	return Handler


def main():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--dir", required=True, help="directory with <endpoint>.json files")
	parser.add_argument("--host", default="127.0.0.1")
	parser.add_argument("--port", type=int, default=8765)
	parser.add_argument("--user", default="svc_hr_export")
	parser.add_argument("--password", default="")
	parser.add_argument("--prefix", default="/hs/hr_export", help="URL prefix before the endpoint name")
	args = parser.parse_args()
	server = ThreadingHTTPServer(
		(args.host, args.port), make_handler(args.dir, args.user, args.password, args.prefix)
	)
	print(f"Serving {args.dir} on http://{args.host}:{args.port}{args.prefix}/<endpoint>")
	server.serve_forever()


if __name__ == "__main__":
	main()
