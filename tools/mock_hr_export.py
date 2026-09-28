#!/usr/bin/env python3
"""A local stand-in for the HTTP services of 1C:ZUP: HR_Export_API and ITAccess.

Serves <dir>/{meta,organizations,departments,employees,absences}.json under /hs/hr_export and,
with --itaccess-dir, <dir>/snapshot_sample.json and <dir>/log_sample.json under /hs/itaccess,
with basic auth, so the whole chain (HR Source → HTTP client → background job → Sync Log) can be
tried without a real ZUP. Only synthetic data: never put real exports here.

    python3 tools/mock_hr_export.py --dir access_registry/tests/fixtures/zup1 \
        --itaccess-dir access_registry/tests/fixtures/itaccess --port 8765 \
        --user svc_hr_export --password secret

HR Source: base URL http://127.0.0.1:8765/hs/hr_export; ITAccess URL
http://127.0.0.1:8765/hs/itaccess; the same user and password for both.
"""

import argparse
import base64
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ENDPOINTS = ("meta", "organizations", "departments", "employees", "absences")


ITACCESS_FILES = {
	"snapshot": ("snapshot_sample.json", "snapshot.json"),
	"log": ("log_sample.json", "log.json"),
}


def _itaccess(directory, endpoint, query):
	for filename in ITACCESS_FILES[endpoint]:
		path = os.path.join(directory, filename)
		if os.path.exists(path):
			break
	with open(path, encoding="utf-8") as fh:
		data = json.load(fh)
	if endpoint == "log":
		start = (parse_qs(query).get("from") or [""])[0]
		if start:
			since = datetime.fromisoformat(start)
			data["from"] = start
			data["events"] = [e for e in data.get("events", []) if datetime.fromisoformat(e["date"]) >= since]
	return data


def make_handler(directory, user, password, prefix, itaccess_dir=None, itaccess_prefix="/hs/itaccess"):
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
			if itaccess_dir and path.startswith(itaccess_prefix):
				endpoint = path[len(itaccess_prefix) :].strip("/")
				if endpoint not in ITACCESS_FILES:
					return self._send(404, {"error": f"unknown endpoint {endpoint}"})
				return self._send(200, _itaccess(itaccess_dir, endpoint, url.query))
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
			self._send(200, data)

		def _send(self, status, data):
			body = json.dumps(data, ensure_ascii=False).encode("utf-8")
			self.send_response(status)
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
	parser.add_argument("--itaccess-dir", help="directory with snapshot_sample.json and log_sample.json")
	args = parser.parse_args()
	handler = make_handler(args.dir, args.user, args.password, args.prefix, args.itaccess_dir)
	server = ThreadingHTTPServer((args.host, args.port), handler)
	print(f"Serving {args.dir} on http://{args.host}:{args.port}{args.prefix}/<endpoint>")
	if args.itaccess_dir:
		print(f"Serving {args.itaccess_dir} on http://{args.host}:{args.port}/hs/itaccess/<snapshot|log>")
	server.serve_forever()


if __name__ == "__main__":
	main()
