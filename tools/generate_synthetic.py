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


if __name__ == "__main__":
	main()
