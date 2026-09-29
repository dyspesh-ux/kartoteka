"""Mirror of shared folder permissions from a Synology NAS (pushed by synology/registry_collect.sh)."""

import hashlib
import json
import traceback
from collections import Counter

import frappe
from frappe.utils import cint, now_datetime

from access_registry.access_catalog.importer import save_changed, sync_child_table
from access_registry.file_shares.synology import ParseError, access_level, decode_upload, parse
from access_registry.settings import get_settings
from access_registry.sync.engine import _format_messages, _switch_user

SAVEPOINT = "file_share_sync"
DERIVED_VERSION = 1
LOG_KIND = "Файловый сервер"
# standard local accounts of DSM that are not a finding by themselves
STANDARD_LOCAL = {"administrators", "admin", "system", "http", "users", "root", "guest"}


class ShareGuardTripped(frappe.ValidationError):
	pass


def src_hash(payload) -> str:
	return hashlib.md5(
		json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
	).hexdigest()


def run_upload(server: str, data: bytes, commit: bool = True):
	"""Parses the collector output and mirrors it in one transaction. Returns the Sync Log."""
	settings = get_settings()
	log = frappe.get_doc(
		{"doctype": "Sync Log", "kind": LOG_KIND, "status": "В процессе", "started": now_datetime()}
	).insert(ignore_permissions=True)
	if commit:
		frappe.db.commit()
	stats, messages = {}, []
	previous_user = frappe.session.user
	_switch_user(settings.sync_user, messages)
	frappe.db.savepoint(SAVEPOINT)
	status = "Ошибка"
	try:
		parsed = parse(decode_upload(data))
		importer = ShareImport(frappe.get_doc("File Server", server))
		stats = importer.run(parsed)
		messages += importer.warnings
		status = "Успех"
	except ShareGuardTripped as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		status = "Остановлен предохранителем"
		messages.append(str(e))
	except ParseError as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(f"Выгрузка не разобрана: {e}")
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		messages.append(traceback.format_exc())
	finally:
		_switch_user(previous_user, None)
	log.reload()
	log.status = status
	log.finished = now_datetime()
	log.stats = json.dumps({"server": server, **stats}, ensure_ascii=False, indent=1, sort_keys=True)
	log.messages = _format_messages(messages)
	log.save(ignore_permissions=True)
	values = {"last_status": f"{status} ({log.name}, {log.finished.strftime('%Y-%m-%d %H:%M')})"}
	if status == "Успех":
		values.update(
			{
				"last_upload": now_datetime(),
				"shares_count": stats.get("shares", 0),
				"folders_count": stats.get("folders", 0),
				"collector_settings": stats.get("collector_settings", ""),
			}
		)
	frappe.db.set_value("File Server", server, values, update_modified=False)
	if commit:
		frappe.db.commit()
	return log


class Principals:
	"""Names as Synology writes them (CORP\\ivanov, @CORP\\buh, ivanov@corp.local, admin) → AD."""

	def __init__(self, server):
		self.default_domain = server.domain
		self.domains = {}
		for d in frappe.get_all("AD Domain", fields=["name", "netbios_name", "dns_name"]):
			for key in (d.name, d.netbios_name, d.dns_name):
				if key:
					self.domains[key.lower()] = d.name
		self.accounts, self.upn = {}, {}
		for a in frappe.get_all(
			"AD Account",
			filters={"missing_in_source": 0},
			fields=["name", "domain", "sam_account_name", "user_principal_name"],
			limit_page_length=0,
		):
			if a.sam_account_name:
				self.accounts[(a.domain, a.sam_account_name.lower())] = a.name
			if a.user_principal_name:
				self.upn[a.user_principal_name.lower()] = a.name
		self.groups = {}
		for g in frappe.get_all(
			"AD Group",
			filters={"missing_in_source": 0},
			fields=["name", "domain", "sam_account_name", "group_name"],
			limit_page_length=0,
		):
			for key in (g.sam_account_name, g.group_name):
				if key:
					self.groups.setdefault((g.domain, key.lower()), g.name)

	def resolve(self, kind: str, name: str) -> dict:
		kind = (kind or "").lower()
		result = {"principal": name or kind, "principal_type": "", "ad_account": None, "ad_group": None}
		if kind in ("everyone", "authenticated_user"):
			result.update(
				principal_type="Все",
				principal=name or ("Все" if kind == "everyone" else "Прошедшие проверку"),
			)
			return result
		if kind == "owner":
			result.update(principal_type="Владелец", principal=name or "Владелец")
			return result
		domain, login = None, name or ""
		if "\\" in login:
			prefix, login = login.split("\\", 1)
			domain = self.domains.get(prefix.lower())
			if not domain:
				result["principal_type"] = "Не найден в AD"
				return result
		elif "@" in login and kind == "user":
			account = self.upn.get(login.lower())
			if account:
				result.update(principal_type="Пользователь AD", ad_account=account)
			else:
				result["principal_type"] = "Не найден в AD"
			return result
		if domain is None:
			result["principal_type"] = "Локальная группа" if kind == "group" else "Локальный пользователь"
			return result
		if kind == "group":
			group = self.groups.get((domain, login.lower()))
			result.update(principal_type="Группа AD" if group else "Не найден в AD", ad_group=group)
		else:
			account = self.accounts.get((domain, login.lower()))
			result.update(
				principal_type="Пользователь AD" if account else "Не найден в AD", ad_account=account
			)
		return result


class ShareImport:
	def __init__(self, server):
		self.server = server
		self.code = server.name
		self.settings = get_settings()
		self.counters = Counter()
		self.warnings: list[str] = []
		self.principals = Principals(server)

	def warn(self, text):
		self.warnings.append(f"Предупреждение: {text}")

	def run(self, parsed: dict) -> dict:
		if not parsed["complete"]:
			raise ParseError(
				"выгрузка оборвалась (нет строки END): загрузка не выполнена, чтобы не пометить папки пропавшими"
			)
		meta = parsed["meta"]
		if meta.get("server") and meta["server"].lower() != self.code.lower():
			self.warn(f"в выгрузке SERVER_CODE={meta['server']}, а загружается в сервер {self.code}")
		for error in parsed["errors"]:
			self.warn(f"сборщик: {error}")
		self.check_guard(len(parsed["folders"]))
		seen_shares = {self.import_share(share) for share in parsed["shares"].values()}
		seen_folders = {self.import_folder(folder) for folder in parsed["folders"]}
		self.mark_missing("File Share", seen_shares)
		self.mark_missing("Folder ACL", seen_folders)
		self.counters["shares"] = len(parsed["shares"])
		self.counters["folders"] = len(parsed["folders"])
		self.counters["collector_settings"] = (
			f"STOP_ON_SAME={meta.get('stop_on_same', '?')}, MAX_DEPTH={meta.get('max_depth', '?')}"
		)
		if meta.get("stop_on_same") == "1":
			self.warn(
				"сборщик работает с STOP_ON_SAME=1: папки с особыми правами внутри папок с такими же правами, "
				"как у родителя, не проверяются. Для полной картины запускайте иногда с STOP_ON_SAME=0"
			)
		return dict(self.counters)

	def check_guard(self, received):
		threshold = cint(self.settings.shrink_threshold_pct)
		actual = frappe.db.count("Folder ACL", {"server": self.code, "missing_in_source": 0})
		if actual < cint(self.settings.guard_min_records):
			return
		if received < actual * (1 - threshold / 100):
			shrink = round(100 * (actual - received) / actual, 1)
			raise ShareGuardTripped(
				f"Загрузка сервера {self.code} остановлена предохранителем: папок в выгрузке {received}, "
				f"в реестре {actual} — сокращение на {shrink}%, допустимо не более {threshold}%. Проверьте SHARES, "
				f"SKIP_SHARES и MAX_DEPTH в сборщике. Если сокращение ожидаемое, временно поднимите «Допустимое "
				f"сокращение выгрузки, %» до {int(shrink) + 1} или выше."
			)

	def load(self, doctype, uid, h):
		if frappe.db.exists(doctype, uid):
			current = frappe.db.get_value(doctype, uid, ["src_hash", "missing_in_source"], as_dict=True)
			if current.src_hash == h and not current.missing_in_source:
				self.counters[f"{doctype}: без изменений"] += 1
				return None
			return frappe.get_doc(doctype, uid)
		doc = frappe.new_doc(doctype)
		doc.uid = uid
		return doc

	def save(self, doc):
		new = doc.is_new()
		doc.missing_in_source = 0
		if save_changed(doc):
			self.counters[f"{doc.doctype}: {'создано' if new else 'изменено'}"] += 1
		else:
			self.counters[f"{doc.doctype}: без изменений"] += 1
		if doc.flags.cut_fields:
			self.warn(f"{doc.name}: слишком длинные значения обрезаны: {', '.join(doc.flags.cut_fields)}")

	def share_uid(self, name):
		return f"{self.code}:{name}"

	def import_share(self, share: dict) -> str:
		uid = self.share_uid(share["name"])
		rows = []
		for p in share["privileges"]:
			resolved = self.principals.resolve("group" if p["group"] else "user", p["name"])
			rows.append({**resolved, "level": p["level"]})
		rows.sort(key=lambda r: (r["principal"], r["level"]))
		h = src_hash({"s": share, "rows": rows, "v": DERIVED_VERSION})
		doc = self.load("File Share", uid, h)
		if doc is None:
			return uid
		doc.share_name = share["name"]
		doc.server = self.code
		doc.path = share.get("path")
		doc.comment = share.get("comment")
		sync_child_table(doc, "privileges", ("principal", "level"), rows)
		doc.src_hash = h
		self.save(doc)
		return uid

	def import_folder(self, folder: dict) -> str:
		share_uid = self.share_uid(folder["share"])
		uid = f"{share_uid}:{hashlib.md5(folder['path'].encode()).hexdigest()[:16]}"
		rows, issues = [], []
		direct, everyone, denied, unknown, local = [], [], [], [], []
		for e in folder["entries"]:
			resolved = self.principals.resolve(e["kind"], e["name"])
			level = access_level(e["rights"], e["allow"])
			rows.append(
				{
					**resolved,
					"allow": "Разрешить" if e["allow"] else "Запретить",
					"level": level,
					"rights": e["rights"],
					"inherited": int(e["inherited"]),
					"applies_to": e["applies_to"],
				}
			)
			if e["inherited"]:
				continue
			label = resolved["principal"]
			if not e["allow"]:
				denied.append(label)
			elif resolved["principal_type"] == "Пользователь AD":
				direct.append(label)
			elif resolved["principal_type"] == "Все":
				everyone.append(label)
			if resolved["principal_type"] == "Не найден в AD":
				unknown.append(label)
			elif (
				resolved["principal_type"] in ("Локальный пользователь", "Локальная группа")
				and label.lower() not in STANDARD_LOCAL
			):
				local.append(label)
		for title, names in (
			("права выданы пользователю напрямую, не через группу", direct),
			("доступ для всех", everyone),
			("запрет", denied),
			("не найдены в AD (удалённые учётки или группы)", unknown),
			("локальные учётки NAS", local),
		):
			if names:
				issues.append(f"{title}: {', '.join(sorted(set(names)))}")
		if not folder["inherit_enabled"] and folder["depth"] > 0:
			issues.append("наследование прав от родителя отключено")
		if folder.get("acl_error"):
			issues.append(f"сборщик не прочитал права: {folder['acl_error']}")
		explicit = sum(1 for e in folder["entries"] if not e["inherited"])
		payload = {"f": folder, "rows": rows, "v": DERIVED_VERSION}
		h = src_hash(payload)
		doc = self.load("Folder ACL", uid, h)
		if doc is None:
			return uid
		doc.path = folder["path"]
		doc.share = share_uid if frappe.db.exists("File Share", share_uid) else None
		doc.server = self.code
		doc.depth = folder["depth"]
		doc.inherit_enabled = int(folder["inherit_enabled"])
		doc.explicit_entries = explicit
		doc.issues = "\n".join(issues)
		sync_child_table(doc, "entries", ("principal", "allow", "rights", "inherited", "applies_to"), rows)
		doc.src_hash = h
		self.save(doc)
		return uid

	def mark_missing(self, doctype, seen):
		for name in frappe.get_all(
			doctype, filters={"server": self.code, "missing_in_source": 0}, pluck="name"
		):
			if name not in seen:
				doc = frappe.get_doc(doctype, name)
				doc.missing_in_source = 1
				doc.save(ignore_permissions=True, ignore_version=False)
				self.counters[f"{doctype}: пропало"] += 1
