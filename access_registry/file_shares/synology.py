"""Parser of the Synology collector output (synology/registry_collect.sh).

The collector sends raw text of `synoshare --get` and `synoacltool -get`; all parsing is here,
where it is tested. Lines:

    SERVER/HOST/DSM <base64>, TIME <iso>, STOP_ON_SAME <0|1>, MAX_DEPTH <n>
    SHARE <b64 name>            then INFO <raw synoshare line>…
    DIR <b64 share> <b64 path> <depth>   then ACL <raw synoacltool line>…
    ERROR <b64 share> <b64 message>
"""

import base64
import gzip
import re

ACL_LINE = re.compile(r"^\s*\[(\d+)\]\s*(.*?)\s*(?:\(level:(\d+)\))?\s*$")
PRIVILEGE_LINE = re.compile(r"^\s*(RW|RO|NA|Deny)\s+list\s*\.*\s*\[(.*)\]\s*$", re.I)
FIELD_LINE = re.compile(r"^\s*(Name|Path|Comment)\s*\.*\s*\[(.*)\]\s*$", re.I)
PRIVILEGE_LEVEL = {"rw": "Изменение", "ro": "Чтение", "na": "Запрет", "deny": "Запрет"}


class ParseError(ValueError):
	pass


def _b64(value: str) -> str:
	try:
		return base64.b64decode(value.encode()).decode("utf-8", "replace")
	except Exception as e:
		raise ParseError(f"не base64: {value[:40]}") from e


def decode_upload(data: bytes) -> str:
	if data[:2] == b"\x1f\x8b":
		data = gzip.decompress(data)
	return data.decode("utf-8", "replace")


def parse(text: str) -> dict:
	lines = text.splitlines()
	if not lines or not lines[0].startswith("#REGISTRY-SYNOLOGY"):
		raise ParseError("это не выгрузка сборщика Synology (нет заголовка #REGISTRY-SYNOLOGY)")
	result = {"meta": {}, "shares": {}, "folders": [], "errors": [], "complete": False}
	share = folder = None
	for line in lines[1:]:
		kind, _, rest = line.partition(" ")
		if kind in ("SERVER", "HOST", "DSM"):
			result["meta"][kind.lower()] = _b64(rest) if rest else ""
		elif kind in ("TIME", "STOP_ON_SAME", "MAX_DEPTH"):
			result["meta"][kind.lower()] = rest.strip()
		elif kind == "SHARE":
			name = _b64(rest)
			share = result["shares"].setdefault(
				name, {"name": name, "path": "", "comment": "", "privileges": []}
			)
			folder = None
		elif kind == "INFO" and share is not None:
			parse_info_line(share, rest)
		elif kind == "DIR":
			parts = rest.split()
			if len(parts) != 3:
				raise ParseError(f"строка DIR: {line[:80]}")
			folder = {
				"share": _b64(parts[0]),
				"path": _b64(parts[1]) or "/",
				"depth": int(parts[2]),
				"inherit_enabled": True,
				"entries": [],
				"acl_error": "",
			}
			result["folders"].append(folder)
		elif kind == "ACL" and folder is not None:
			parse_acl_line(folder, rest)
		elif kind == "ERROR":
			parts = rest.split()
			result["errors"].append(" ".join(_b64(p) for p in parts))
		elif kind == "END":
			result["complete"] = True
	return result


def parse_info_line(share: dict, text: str):
	m = PRIVILEGE_LINE.match(text)
	if m:
		level = PRIVILEGE_LEVEL[m.group(1).lower()]
		for item in split_list(m.group(2)):
			is_group = item.startswith("@")
			share["privileges"].append({"name": item.lstrip("@"), "group": is_group, "level": level})
		return
	m = FIELD_LINE.match(text)
	if m:
		key = m.group(1).lower()
		if key in ("path", "comment"):
			share[key] = m.group(2).strip()


def split_list(value: str) -> list[str]:
	return [v.strip() for v in value.split(",") if v.strip()]


def parse_acl_line(folder: dict, text: str):
	if text.startswith("Archive:"):
		flags = {f.strip() for f in text.split(":", 1)[1].split(",")}
		folder["inherit_enabled"] = "is_inherit" in flags
		return
	m = ACL_LINE.match(text)
	if not m:
		if text.strip() and not text.startswith("ACL version") and "error" in text.lower():
			folder["acl_error"] = text.strip()
		return
	body, level = m.group(2), int(m.group(3) or 0)
	parts = body.split(":")
	if len(parts) < 5:
		return
	kind, rights, applies = parts[0], parts[-2], parts[-1]
	allow = parts[-3]
	name = ":".join(parts[1:-3])
	folder["entries"].append(
		{
			"kind": kind,  # user, group, everyone, owner, authenticated_user, system
			"name": name,
			"allow": allow == "allow",
			"rights": rights,
			"applies_to": applies,
			"inherited": level > 0,
		}
	)


def access_level(rights: str, allow: bool = True) -> str:
	"""rwxpdDaARWcCo → a readable level."""
	if not allow:
		return "Запрет"
	if "C" in rights or "o" in rights:
		return "Полный доступ"
	if any(ch in rights for ch in "wpdD"):
		return "Изменение"
	if "r" in rights:
		return "Чтение"
	return "Особые права"


LEVEL_RANK = {"Запрет": 0, "Особые права": 1, "Чтение": 2, "Изменение": 3, "Полный доступ": 4}
