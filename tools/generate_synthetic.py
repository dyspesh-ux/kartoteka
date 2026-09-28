#!/usr/bin/env python3
"""Generates a large synthetic HR_Export_API dataset (for load tests and demos).

    python3 tools/generate_synthetic.py --out /tmp/zup_big --employees 5000 --departments 400

All names, dates and GUIDs are random; nothing here comes from a real database.
"""

import argparse
import datetime
import json
import os
import random
import uuid

LAST = [
	"Иванов",
	"Петров",
	"Сидоров",
	"Смирнов",
	"Кузнецов",
	"Попов",
	"Васильев",
	"Соколов",
	"Михайлов",
	"Новиков",
	"Фёдоров",
	"Морозов",
	"Волков",
	"Алексеев",
	"Лебедев",
	"Семёнов",
	"Егоров",
	"Павлов",
	"Козлов",
	"Степанов",
	"Николаев",
	"Орлов",
	"Андреев",
	"Макаров",
	"Никитин",
	"Захаров",
]
FIRST_M = ["Иван", "Пётр", "Алексей", "Сергей", "Дмитрий", "Андрей", "Михаил", "Олег", "Николай", "Юрий"]
FIRST_F = ["Анна", "Мария", "Елена", "Ольга", "Наталья", "Татьяна", "Ирина", "Светлана", "Юлия", "Дарья"]
MIDDLE_M = ["Иванович", "Петрович", "Сергеевич", "Алексеевич", "Андреевич", "Михайлович", "Олегович"]
MIDDLE_F = ["Ивановна", "Петровна", "Сергеевна", "Алексеевна", "Андреевна", "Михайловна", "Олеговна"]
POSITIONS = [
	"Руководитель направления",
	"Начальник отдела",
	"Заместитель начальника отдела",
	"Главный специалист",
	"Ведущий специалист",
	"Специалист",
	"Менеджер",
	"Бухгалтер",
	"Инженер",
	"Оператор",
	"Кладовщик",
	"Водитель",
	"Директор филиала",
	"Заведующий складом",
]
DEPT_NAMES = [
	"Отдел продаж",
	"Бухгалтерия",
	"Склад",
	"Отдел кадров",
	"ИТ-отдел",
	"Юридический отдел",
	"Отдел закупок",
	"Смена",
	"Участок",
	"Сектор",
	"Группа",
]


def guid(rng):
	return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def rand_date(rng, start, end):
	return (start + datetime.timedelta(days=rng.randint(0, (end - start).days))).isoformat()


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--out", required=True)
	parser.add_argument("--employees", type=int, default=5000)
	parser.add_argument("--departments", type=int, default=400)
	parser.add_argument("--organizations", type=int, default=5)
	parser.add_argument("--seed", type=int, default=42)
	parser.add_argument(
		"--itaccess",
		action="store_true",
		help="also write snapshot.json/log.json of the ITAccess service for this ZUP base and an accounting base",
	)
	parser.add_argument(
		"--ad",
		action="store_true",
		help="also write ad/directory.json: Active Directory accounts and groups of the same people",
	)
	args = parser.parse_args()
	rng = random.Random(args.seed)
	os.makedirs(args.out, exist_ok=True)
	today = datetime.date.today()

	orgs = []
	for i in range(args.organizations):
		g = guid(rng)
		orgs.append(
			{
				"ОрганизацияGUID": g,
				"Префикс": f"O{i}",
				"Наименование": f"Организация {i} ООО",
				"НаименованиеПолное": f'Общество с ограниченной ответственностью "Организация {i}"',
				"ИНН": f"77{i:08d}",
				"КПП": f"77{i:02d}01001",
				"ГоловнаяОрганизацияGUID": orgs[0]["ОрганизацияGUID"] if orgs else g,
			}
		)

	deps = []
	for i in range(args.departments):
		org = rng.choice(orgs)
		same_org = [d for d in deps if d["ОрганизацияGUID"] == org["ОрганизацияGUID"]]
		parent = rng.choice(same_org)["ПодразделениеGUID"] if same_org and rng.random() < 0.8 else None
		deps.append(
			{
				"ПодразделениеGUID": guid(rng),
				"Код": f"{org['Префикс']}ЗП-{i:05d}",
				"Наименование": f"{rng.choice(DEPT_NAMES)} {i}",
				"РодительGUID": parent,
				"ОрганизацияGUID": org["ОрганизацияGUID"],
				"Организация": org["Наименование"],
				"РуководительФизЛицоGUID": None,
				"Руководитель": None,
			}
		)

	positions = {p: guid(rng) for p in POSITIONS}
	emps, absences = [], []
	people = []
	for i in range(args.employees):
		if people and rng.random() < 0.05:
			person = rng.choice(people)  # second job of the same person
			kind = "ВнутреннееСовместительство"
		else:
			female = rng.random() < 0.5
			person = {
				"g": guid(rng),
				"last": rng.choice(LAST) + ("а" if female else ""),
				"first": rng.choice(FIRST_F if female else FIRST_M),
				"middle": rng.choice(MIDDLE_F if female else MIDDLE_M),
				"birth": rand_date(rng, datetime.date(1960, 1, 1), datetime.date(2004, 12, 31)),
			}
			people.append(person)
			kind = "ОсновноеМестоРаботы" if rng.random() < 0.93 else "Совместительство"
		dep = rng.choice(deps)
		pos = rng.choice(POSITIONS)
		hire = rand_date(rng, datetime.date(2005, 1, 1), today)
		fired = rng.random() < 0.25
		term = rand_date(rng, datetime.date.fromisoformat(hire), today) if fired else None
		cat, code, state, working, af, at = "Работает", "Работа", "Работа", True, None, None
		roll = rng.random()
		if not fired and roll < 0.03:
			cat, code, state, working = (
				"ОтпускПоУходу",
				"ОтпускПоУходуЗаРебенком",
				"Отпуск по уходу за ребенком",
				rng.random() < 0.2,
			)
			af, at = (
				rand_date(rng, today - datetime.timedelta(days=500), today),
				rand_date(rng, today, today + datetime.timedelta(days=700)),
			)
		elif not fired and roll < 0.10:
			cat, code, state, working = "Отпуск", "ОтпускОсновной", "Основной отпуск", False
			af = rand_date(rng, today - datetime.timedelta(days=10), today)
			at = (datetime.date.fromisoformat(af) + datetime.timedelta(days=14)).isoformat()
		elif fired:
			cat, code, state, working = "Уволен", "Увольнение", "Увольнение", False
		emp_guid = guid(rng)
		emps.append(
			{
				"СотрудникGUID": emp_guid,
				"ФизЛицоGUID": person["g"],
				"ТабельныйНомер": f"{i:05d}",
				"Фамилия": person["last"],
				"Имя": person["first"],
				"Отчество": person["middle"],
				"ДатаРождения": person["birth"],
				"ОрганизацияGUID": dep["ОрганизацияGUID"],
				"Организация": dep["Организация"],
				"ПодразделениеGUID": dep["ПодразделениеGUID"],
				"Подразделение": dep["Наименование"],
				"ДолжностьGUID": positions[pos],
				"Должность": pos,
				"ДатаПриема": hire,
				"ДатаУвольнения": term,
				"ВидЗанятостиКод": kind,
				"ВидЗанятости": kind,
				"Состояние": "Уволен" if fired else "Работает",
				"КадровоеСостояниеКод": code,
				"КадровоеСостояние": state,
				"Категория": cat,
				"ОтсутствуетС": af,
				"ОтсутствуетПо": at,
				"ОкончаниеПредположительно": None,
				"ДоляНеполногоВремени": None,
				"ФактическиРаботает": working,
			}
		)
		if af:
			absences.append(
				{
					"СотрудникGUID": emp_guid,
					"ФизЛицоGUID": person["g"],
					"ФИО": "",
					"Код": code,
					"Состояние": state,
					"Категория": cat,
					"ДатаНачала": af,
					"ДатаОкончания": at,
					"ОкончаниеПредположительно": None,
					"ОрганизацияGUID": dep["ОрганизацияGUID"],
					"Организация": dep["Организация"],
					"ИсточникОрганизации": "КадровыеДанные",
					"ПодразделениеGUID": dep["ПодразделениеGUID"],
					"Подразделение": dep["Наименование"],
					"ДолжностьGUID": positions[pos],
					"Должность": pos,
					"ВидЗанятостиКод": kind,
					"ВидЗанятости": kind,
					"ДоляНеполногоВремени": None,
					"ФактическиРаботает": working,
				}
			)
		if rng.random() < 0.02 and not dep["РуководительФизЛицоGUID"]:
			dep["РуководительФизЛицоGUID"] = person["g"]

	meta = {
		"ВидыСостояний": [
			{"Код": "Работа", "Категория": "Работает"},
			{"Код": "ОтпускПоУходуЗаРебенком", "Категория": "ОтпускПоУходу"},
		]
	}
	for name, data in (
		("meta", meta),
		("organizations", orgs),
		("departments", deps),
		("employees", emps),
		("absences", absences),
	):
		with open(os.path.join(args.out, f"{name}.json"), "w", encoding="utf-8") as fh:
			json.dump(data, fh, ensure_ascii=False)
	print(
		f"{len(orgs)} organizations, {len(deps)} departments, {len(emps)} employees, "
		f"{len(people)} people, {len(absences)} absences → {args.out}"
	)
	if args.itaccess:
		for name, zup in (("itaccess_zup", True), ("itaccess_bp", False)):
			folder = os.path.join(args.out, name)
			os.makedirs(folder, exist_ok=True)
			snapshot, log = itaccess_snapshot(rng, emps, orgs, zup)
			for fname, data in (("snapshot.json", snapshot), ("log.json", log)):
				with open(os.path.join(folder, fname), "w", encoding="utf-8") as fh:
					json.dump(data, fh, ensure_ascii=False)
			print(f"{len(snapshot['users'])} 1C users → {folder}")
	if args.ad:
		folder = os.path.join(args.out, "ad")
		os.makedirs(folder, exist_ok=True)
		directory = ad_directory(random.Random(args.seed + 1), emps, orgs)
		with open(os.path.join(folder, "directory.json"), "w", encoding="utf-8") as fh:
			json.dump(directory, fh, ensure_ascii=False)
		print(f"{len(directory['users'])} AD accounts, {len(directory['groups'])} groups → {folder}")


PROFILE_SETS = {
	True: [
		("Кадровик", ["ДобавлениеИзменениеКадровыхДанных", "ЧтениеКадровыхДанных"]),
		("Расчетчик", ["ДобавлениеИзменениеНачислений", "ЧтениеНачислений"]),
		("Руководитель", ["ЧтениеКадровыхДанных", "ЧтениеОтчетов"]),
		("Сотрудник", ["БазовыеПраваБСП", "ЧтениеЛичныхДанных"]),
		("Администратор", ["ПолныеПрава", "АдминистраторСистемы"]),
	],
	False: [
		("Бухгалтер", ["ДобавлениеИзменениеПроводок", "ЧтениеОтчетов"]),
		("Главный бухгалтер", ["ДобавлениеИзменениеПроводок", "ЗакрытиеМесяца", "ЧтениеОтчетов"]),
		("Казначей", ["ДобавлениеИзменениеПлатежей", "ЧтениеОтчетов"]),
		("Менеджер по закупкам", ["ДобавлениеИзменениеЗакупок"]),
		("Администратор", ["ПолныеПрава", "АдминистраторСистемы"]),
	],
}
EXTRA_ROLES = [
	"ИнтерактивноеОткрытиеВнешнихОтчетовИОбработок",
	"ИспользованиеРегламентированнойОтчетности",
	"ПолныеПрава",
]
TRANSLIT = dict(
	zip(
		"абвгдеёжзийклмнопрстуфхцчшщыэюя",
		"a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh sch y e yu ya".split(),
		strict=True,
	)
)


def translit(text):
	return "".join(TRANSLIT.get(ch, "") for ch in text.lower())


def itaccess_snapshot(rng, emps, orgs, zup):
	"""Synthetic ITAccess /snapshot and /log.

	ZUP base: users carry the person GUIDs of the HR export. Accounting base: own GUIDs, mostly without a
	person, so employees are found by full name. Some dismissed people keep the right to log in.
	"""
	profiles = [
		{
			"id": guid(rng),
			"name": name,
			"deleted": False,
			"supplied": True,
			"roles": [{"name": r, "title": r} for r in roles],
		}
		for name, roles in PROFILE_SETS[zup]
	]
	people = {}
	for e in emps:
		people.setdefault(e["ФизЛицоGUID"], e)
	chosen = rng.sample(list(people.values()), k=min(len(people), 140 if zup else 90))
	users, rights = [], []
	for e in chosen:
		fio = f"{e['Фамилия']} {e['Имя']} {e['Отчество']}"
		fired = bool(e["ДатаУвольнения"])
		allowed = (not fired) or rng.random() < 0.3
		ad = f"{translit(e['Фамилия'])}.{translit(e['Имя'][0])}" if rng.random() < 0.8 else None
		my_profiles = rng.sample(profiles[:-1], k=rng.randint(1, 2))
		if rng.random() < 0.03:
			my_profiles.append(profiles[-1])
		roles = sorted({r["name"] for p in my_profiles for r in p["roles"]})
		if rng.random() < 0.08:
			roles.append(rng.choice(EXTRA_ROLES))
		user_id = guid(rng)
		users.append(
			{
				"id": user_id,
				"name": fio,
				"invalid": fired and not allowed,
				"service": False,
				"deleted": False,
				"person_id": e["ФизЛицоGUID"] if zup else None,
				"person_name": fio if zup else "",
				"department_id": None,
				"department_name": e["Подразделение"],
				"ib": {
					"ib_id": guid(rng),
					"login": f"{e['Фамилия']} {e['Имя'][0]}.{e['Отчество'][0]}.",
					"full_name": fio,
					"auth_standard": ad is None,
					"auth_os": ad is not None,
					"auth_openid": False,
					"os_user": f"\\\\CORP\\{ad}" if ad else "",
					"ad_domain": "corp" if ad else None,
					"ad_login": ad,
					"login_allowed": allowed,
					"roles": roles,
				},
			}
		)
		org = rng.choice(orgs)
		only_org = rng.random() < 0.5
		restriction = {
			"kind": "Организации",
			"source": "access_group",
			"mode": "only" if only_org else "all",
			"values": [{"id": org["ОрганизацияGUID"], "name": org["Наименование"]}] if only_org else [],
		}
		rights.append(
			{
				"user_id": user_id,
				"user_name": fio,
				"profiles": [
					{
						"profile_id": p["id"],
						"profile_name": p["name"],
						"access_group_id": guid(rng),
						"access_group_name": p["name"],
						"direct": True,
						"via_name": fio,
						"restrictions": [restriction],
					}
					for p in my_profiles
				],
				"organizations": {"all": not only_org, "list": [org["Наименование"]] if only_org else []},
			}
		)
	for n in range(3):
		users.append(
			{
				"id": guid(rng),
				"name": f"Служебный обмен {n + 1}",
				"invalid": False,
				"service": True,
				"deleted": False,
				"person_id": None,
				"person_name": "",
				"department_id": None,
				"department_name": "",
				"ib": {
					"ib_id": guid(rng),
					"login": f"svc_exchange_{n + 1}",
					"full_name": "",
					"auth_standard": True,
					"auth_os": False,
					"auth_openid": False,
					"os_user": "",
					"ad_domain": None,
					"ad_login": None,
					"login_allowed": True,
					"roles": ["ПолныеПрава"],
				},
			}
		)
	orphans = [
		{
			"ib_id": guid(rng),
			"login": "robot",
			"full_name": "",
			"auth_standard": True,
			"auth_os": False,
			"auth_openid": False,
			"os_user": "",
			"ad_domain": None,
			"ad_login": None,
			"login_allowed": True,
			"roles": ["ПолныеПрава"],
		}
	]
	now = datetime.datetime.now().replace(microsecond=0)
	events = [
		{
			"date": (now - datetime.timedelta(hours=h)).isoformat() + "+03:00",
			"who": "Администратор",
			"event": rng.choice(["_$User$_.Update", "_$Data$_.Update", "_$Data$_.New"]),
			"object_type": rng.choice(["", "Справочник.ГруппыДоступа", "Справочник.Пользователи"]),
			"object": rng.choice(users)["name"],
			"comment": "",
			"host": "PC-IT-01",
		}
		for h in range(1, 25)
	]
	snapshot = {
		"base": "synthetic",
		"generated_at": now.isoformat() + "+03:00",
		"users": users,
		"ib_orphans": orphans,
		"profiles": profiles,
		"user_rights": rights,
	}
	return snapshot, {"base": "synthetic", "from": "", "events": events}


AD_BASE = "DC=corp,DC=example,DC=local"


def ad_directory(rng, emps, orgs):
	"""Synthetic result of the LDAP read (the shape of ldap_client.fetch_directory).

	Logins follow the ITAccess generator (фамилия.и), so 1C users find their AD accounts. Some
	dismissed people keep an enabled account, some names are written short and are not matched.
	"""
	now = datetime.datetime.now().replace(microsecond=0)

	def when(days_ago):
		return (now - datetime.timedelta(days=days_ago)).isoformat()

	def group(cn, group_type, description):
		return {
			"objectGUID": guid(rng),
			"distinguishedName": f"CN={cn},OU=Группы,{AD_BASE}",
			"cn": cn,
			"sAMAccountName": cn,
			"description": description,
			"groupType": group_type,
			"managedBy": None,
			"whenCreated": when(2000),
			"whenChanged": when(30),
		}

	security_global, security_local, distribution = -2147483646, -2147483644, 2
	groups = [
		group("GG_1C_ZUP_Users", security_global, "Пользователи 1С:ЗУП"),
		group("GG_1C_BP_Users", security_global, "Пользователи 1С:Бухгалтерии"),
		group("DL_Share_Buh_RW", security_local, "Папка бухгалтерии: запись"),
		group("GG_VPN", security_global, "Удалённый доступ"),
		group("Рассылка всем", distribution, None),
	]
	org_groups = {
		o["ОрганизацияGUID"]: group(f"GG_Staff_{o['Префикс']}", security_global, o["Наименование"])
		for o in orgs
	}
	groups += list(org_groups.values())
	by_cn = {g["cn"]: g["distinguishedName"] for g in groups}

	people = {}
	for e in emps:
		current = people.get(e["ФизЛицоGUID"])
		if current is None or (current["ДатаУвольнения"] and not e["ДатаУвольнения"]):
			people[e["ФизЛицоGUID"]] = e
	users, logins = [], set()
	for e in people.values():
		fired = bool(e["ДатаУвольнения"])
		if fired and rng.random() < 0.4:
			continue  # the account is already deleted
		login = base = f"{translit(e['Фамилия'])}.{translit(e['Имя'][0])}"
		n = 1
		while login in logins:
			n += 1
			login = f"{base}{n}"
		logins.add(login)
		fio = f"{e['Фамилия']} {e['Имя']} {e['Отчество']}"
		if rng.random() < 0.03:
			fio = f"{e['Фамилия']} {e['Имя'][0]}. {e['Отчество'][0]}."  # not matched by full name
		enabled = not fired or rng.random() < 0.3
		member_of = [by_cn["Рассылка всем"], org_groups[e["ОрганизацияGUID"]]["distinguishedName"]]
		for cn, chance in (
			("GG_1C_ZUP_Users", 0.15),
			("GG_1C_BP_Users", 0.2),
			("DL_Share_Buh_RW", 0.1),
			("GG_VPN", 0.3),
		):
			if rng.random() < chance:
				member_of.append(by_cn[cn])
		roll = rng.random()
		days = rng.randint(120, 400) if roll < 0.1 or fired else rng.randint(0, 20)
		user = ad_user(
			rng,
			login,
			fio,
			e["Организация"],
			512 if enabled else 514,
			member_of,
			None if roll < 0.03 else when(days),
			when,
		)
		user.update({"title": e["Должность"], "department": e["Подразделение"], "company": e["Организация"]})
		users.append(user)
	for n in range(5):
		users.append(
			ad_user(
				rng, f"svc_app{n + 1}", f"svc_app{n + 1}", "Служебные", 66048, [], when(1), when, service=True
			)
		)
	return {"users": users, "groups": groups}


def ad_user(rng, login, display_name, ou, uac, member_of, last_logon, when, service=False):
	container = f"OU={ou}" if service else f"OU={ou},OU=Сотрудники"
	return {
		"objectGUID": guid(rng),
		"distinguishedName": f"CN={display_name},{container},{AD_BASE}",
		"sAMAccountName": login,
		"userPrincipalName": f"{login}@corp.example.local",
		"displayName": display_name,
		"givenName": None,
		"sn": None,
		"middleName": None,
		"mail": None if service else f"{login}@example.com",
		"title": None,
		"department": None,
		"company": None,
		"manager": None,
		"employeeNumber": None,
		"employeeID": None,
		"userAccountControl": uac,
		"lockoutTime": None,
		"pwdLastSet": when(rng.randint(1, 200)),
		"lastLogonTimestamp": last_logon,
		"accountExpires": None,
		"whenCreated": when(rng.randint(200, 3000)),
		"whenChanged": when(rng.randint(0, 30)),
		"memberOf": sorted(member_of),
	}


if __name__ == "__main__":
	main()
