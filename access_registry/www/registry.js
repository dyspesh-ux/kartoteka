/* Access Registry app (/registry): a single page for security, audit and rights controllers.
   No build step and no external libraries: data comes from access_registry.registry.api. */
(() => {
	"use strict";

	const API = "/api/method/access_registry.registry.api.";
	const $app = document.getElementById("app");
	const DESK = $app.dataset.desk === "1";
	const state = { boot: null, dashboard: null, peopleFilters: { query: "", organization: "", status: "Работает", flag: "" } };

	// ------------------------------------------------------------------ helpers

	const esc = (v) =>
		v === null || v === undefined
			? ""
			: String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
	const enc = encodeURIComponent;
	const fmtNum = (n) => new Intl.NumberFormat("ru-RU").format(n || 0);
	const pad = (n) => String(n).padStart(2, "0");
	function parseDate(v) {
		if (!v) return null;
		const d = new Date(String(v).replace(" ", "T"));
		return isNaN(d) ? null : d;
	}
	const fmtDate = (v) => {
		const d = parseDate(v);
		return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}` : "";
	};
	const fmtDateTime = (v) => {
		const d = parseDate(v);
		return d ? `${fmtDate(v)} ${pad(d.getHours())}:${pad(d.getMinutes())}` : "";
	};
	const daysAgo = (v) => {
		const d = parseDate(v);
		return d ? Math.floor((Date.now() - d.getTime()) / 86400000) : null;
	};
	const initials = (name) =>
		(name || "?")
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0])
			.join("")
			.toUpperCase();
	const deskUrl = (doctype, name) => `/app/${doctype.toLowerCase().replace(/ /g, "-")}/${enc(name)}`;
	const plural = (n, one, few, many) => {
		const m10 = n % 10, m100 = n % 100;
		if (m10 === 1 && m100 !== 11) return one;
		if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
		return many;
	};

	async function api(method, args = {}, post = false) {
		const options = { headers: { Accept: "application/json" }, credentials: "same-origin" };
		let url = API + method;
		if (post) {
			options.method = "POST";
			options.headers["Content-Type"] = "application/json";
			options.headers["X-Frappe-CSRF-Token"] = (window.frappe && window.frappe.csrf_token) || "";
			options.body = JSON.stringify(args);
		} else {
			const qs = Object.entries(args)
				.filter(([, v]) => v !== undefined && v !== null && v !== "")
				.map(([k, v]) => `${enc(k)}=${enc(v)}`)
				.join("&");
			if (qs) url += "?" + qs;
		}
		const response = await fetch(url, options);
		let data = {};
		try {
			data = await response.json();
		} catch (e) {
			/* not JSON */
		}
		if (response.status === 403 && /Login|Guest|session/i.test(JSON.stringify(data))) {
			location.href = "/login?redirect-to=/registry";
		}
		if (!response.ok) {
			let text = data._error_message || data.exception || `HTTP ${response.status}`;
			try {
				const messages = JSON.parse(data._server_messages || "[]").map((m) => JSON.parse(m).message);
				if (messages.length) text = messages.join("; ");
			} catch (e) {
				/* keep text */
			}
			throw new Error(String(text).replace(/<[^>]+>/g, ""));
		}
		return data.message;
	}

	function storage(key, value) {
		try {
			if (value === undefined) return localStorage.getItem(key);
			localStorage.setItem(key, value);
		} catch (e) {
			return null;
		}
	}

	function toast(text) {
		const el = document.createElement("div");
		el.className = "toast";
		el.textContent = text;
		document.body.appendChild(el);
		setTimeout(() => el.remove(), 3200);
	}

	const ICONS = {
		lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
		home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v10h14V10"/>',
		people: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14.7c1.9.7 3.1 2.4 3.5 5.3"/>',
		key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9"/><path d="m17 6 3 3"/><path d="m14 9 2 2"/>',
		roles: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 9h8M8 13h8M8 17h5"/>',
		flow: '<rect x="3" y="3" width="7" height="6" rx="1.5"/><rect x="14" y="15" width="7" height="6" rx="1.5"/><path d="M6.5 9v4a2 2 0 0 0 2 2H14"/>',
		shield: '<path d="M12 3 4.5 6v6c0 4.5 3.2 8 7.5 9 4.3-1 7.5-4.5 7.5-9V6z"/><path d="m9 12 2 2 4-4"/>',
		chart: '<path d="M4 4v16h16"/><path d="m7 15 4-4 3 3 5-6"/>',
		report: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/><path d="M9 17v-4M12 17v-6M15 17v-2"/>',
		db: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
		search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
		moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
		download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
		refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
		menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
		check: '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m8 12 3 3 5-6"/>',
		external: '<path d="M14 4h6v6"/><path d="m20 4-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
	};
	const icon = (name) => `<svg viewBox="0 0 24 24">${ICONS[name] || ""}</svg>`;

	const STATUS_TONE = { Работает: "t-green", Уволен: "t-red", "Не принят": "", "Нет в выгрузке": "t-amber", Увольняется: "t-amber" };
	const RECON_TONE = {
		"Не хватает": "t-amber",
		Лишнее: "t-red",
		"Лишнее: не работает": "t-red",
		Исключение: "t-violet",
		Соответствует: "t-green",
	};
	const RISK_TONE = { Критичный: "t-red", Критичная: "t-red", Высокий: "t-amber", Высокая: "t-amber", Средний: "t-blue", Средняя: "t-blue", Низкий: "" };
	const SYSTEM_TONE = { "1С": "t-amber", AD: "t-blue", "Active Directory": "t-blue", Битрикс24: "t-violet", Другое: "" };

	const pill = (text, tone) => (text ? `<span class="pill ${tone || ""}">${esc(text)}</span>` : "");
	const statusPill = (s) => pill(s, STATUS_TONE[s]);
	const systemBadge = (s) => (s ? `<span class="badge ${SYSTEM_TONE[s] || (String(s).startsWith("Битрикс24") ? "t-violet" : String(s).startsWith("AD") ? "t-blue" : "")}">${esc(s)}</span>` : "");

	// ------------------------------------------------------------------ table

	/* columns: [{key, label, type, render(row)}]; types match access_registry.registry.api._column */
	function cell(col, row) {
		const v = row[col.key];
		if (col.render) return col.render(row);
		switch (col.type) {
			case "person":
				return row.person
					? `<a class="person-cell" href="#/person/${enc(row.person)}"><span class="avatar">${esc(initials(v))}</span><span>${esc(v)}</span></a>`
					: `<span class="person-cell"><span class="avatar gray">${esc(initials(v))}</span><span>${esc(v)}</span></span>`;
			case "status":
				return statusPill(v);
			case "badge":
				return systemBadge(v);
			case "risk":
				return pill(v, RISK_TONE[v]);
			case "recon":
				return pill(v, RECON_TONE[v]);
			case "entitlement":
				return row.entitlement ? `<a href="#/entitlement/${enc(row.entitlement)}">${esc(v)}</a>` : esc(v);
			case "process":
				return row.process ? `<a href="#/process/${enc(row.process)}">${esc(v)}</a>` : esc(v);
			case "ref":
				return row.ref && DESK ? `<a href="${deskUrl(row.ref_doctype, row.ref)}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(v);
			case "datetime": {
				if (!v) return `<span class="muted">никогда</span>`;
				const days = daysAgo(v);
				return `${esc(fmtDateTime(v))}${days > 30 ? ` <span class="muted small">(${days} дн.)</span>` : ""}`;
			}
			case "date":
				return esc(fmtDate(v));
			case "deadline": {
				if (!v) return `<span class="muted">бессрочно</span>`;
				const days = -daysAgo(v);
				const tone = days < 0 ? "t-red" : days <= 14 ? "t-amber" : "";
				return pill(fmtDate(v), tone);
			}
			case "number":
				return v === null || v === undefined || v === "" ? "" : fmtNum(v);
			case "check":
				return v ? "✓" : "";
			case "link": {
				if (v === null || v === undefined || v === "") return "";
				if (col.app_link === "person") return `<a href="#/person/${enc(v)}">карточка</a>`;
				if (col.app_link) return `<a href="#/${col.app_link}/${enc(v)}">${esc(v)}</a>`;
				return DESK && col.doctype ? `<a href="${deskUrl(col.doctype, v)}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(v);
			}
			case "problems":
				return `<span style="color:var(--red)">${esc(v)}</span>`;
			default:
				return esc(v);
		}
	}

	function plainValue(col, row) {
		const v = row[col.key];
		if (col.csv) return col.csv(row);
		if (["datetime"].includes(col.type)) return fmtDateTime(v);
		if (["date", "deadline"].includes(col.type)) return fmtDate(v);
		if (col.type === "check") return v ? "да" : "";
		if (Array.isArray(v)) return v.join(", ");
		return v === null || v === undefined ? "" : String(v);
	}

	function exportCsv(columns, rows, name) {
		const quote = (s) => `"${String(s).replace(/"/g, '""')}"`;
		const lines = [columns.map((c) => quote(c.label)).join(";")].concat(
			rows.map((r) => columns.map((c) => quote(plainValue(c, r))).join(";"))
		);
		const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `${name || "реестр"}-${fmtDate(new Date().toISOString()).split(".").reverse().join("-")}.csv`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}

	/* A sortable, filterable table with CSV export, rendered into a container element. */
	function table(container, { columns, rows, name, empty, filter = true, pageSize = 100, actions }) {
		const st = { sort: null, dir: 1, query: "", shown: pageSize };
		const draw = () => {
			let list = rows;
			if (st.query) {
				const q = st.query.toLowerCase();
				list = list.filter((r) => columns.some((c) => plainValue(c, r).toLowerCase().includes(q)));
			}
			if (st.sort) {
				const col = columns.find((c) => c.key === st.sort);
				list = [...list].sort((a, b) => {
					const x = plainValue(col, a), y = plainValue(col, b);
					const nx = parseFloat(x), ny = parseFloat(y);
					const cmp = !isNaN(nx) && !isNaN(ny) && col.type === "number" ? nx - ny : x.localeCompare(y, "ru");
					return cmp * st.dir;
				});
			}
			const head = columns
				.map((c) => `<th data-key="${esc(c.key)}">${esc(c.label)}${st.sort === c.key ? `<span class="arrow">${st.dir > 0 ? "↑" : "↓"}</span>` : ""}</th>`)
				.join("") + (actions ? "<th></th>" : "");
			const body = list
				.slice(0, st.shown)
				.map(
					(r, i) =>
						`<tr>${columns.map((c) => `<td class="${c.type === "number" ? "num" : ""}">${cell(c, r)}</td>`).join("")}${
							actions ? `<td class="nowrap">${actions(r, i)}</td>` : ""
						}</tr>`
				)
				.join("");
			container.querySelector(".tbl").innerHTML = rows.length
				? `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
					<div class="table-foot"><span>${fmtNum(list.length)} ${plural(list.length, "запись", "записи", "записей")}${
						list.length !== rows.length ? ` из ${fmtNum(rows.length)}` : ""
					}</span>${list.length > st.shown ? `<button class="btn small more">Показать ещё</button>` : ""}</div>`
				: `<div class="empty"><b>${esc(empty || "Ничего не найдено")}</b></div>`;
			container.querySelectorAll("th[data-key]").forEach((th) =>
				th.addEventListener("click", () => {
					const key = th.dataset.key;
					st.dir = st.sort === key ? -st.dir : 1;
					st.sort = key;
					draw();
				})
			);
			const more = container.querySelector(".more");
			if (more) more.addEventListener("click", () => ((st.shown += pageSize * 5), draw()));
			container._rows = list;
		};
		container.innerHTML = `
			${filter && rows.length ? `<div class="toolbar" style="padding:14px 14px 0">
				<input class="field tbl-filter" placeholder="Фильтр по таблице" style="flex:1;max-width:360px">
				<button class="btn small tbl-csv">${icon("download")} CSV</button></div>` : ""}
			<div class="tbl"></div>`;
		const input = container.querySelector(".tbl-filter");
		if (input) input.addEventListener("input", () => ((st.query = input.value.trim()), (st.shown = pageSize), draw()));
		const csv = container.querySelector(".tbl-csv");
		if (csv) csv.addEventListener("click", () => exportCsv(columns, container._rows || rows, name));
		draw();
	}

	// ------------------------------------------------------------------ shell

	const NAV = [
		["home", "#/", "Обзор"],
		["chart", "#/management", "Руководству"],
		["people", "#/people", "Сотрудники"],
		["shield", "#/control", "Контроль", "control"],
		["key", "#/access", "Права доступа"],
		["roles", "#/roles", "Роли доступа"],
		["flow", "#/processes", "Бизнес-процессы"],
		["check", "#/reviews", "Пересмотр доступа", "reviews"],
		["report", "#/reports", "Отчёты"],
		["db", "#/sources", "Источники"],
	];
	// menu item → section of the app (app_access.SECTIONS)
	const NAV_SECTION = { "#/": "overview", "#/management": "management", "#/people": "people", "#/control": "control", "#/access": "access", "#/reports": "reports",
		"#/roles": "roles", "#/processes": "processes", "#/sources": "sources" };
	const canSee = (section) => ((state.boot.can.sections || {})[section] || 0) > 0;
	const firstPage = () => {
		const href = Object.keys(NAV_SECTION).find((h) => canSee(NAV_SECTION[h]));
		return href ? href.slice(1) : "/reviews";
	};

	function renderShell() {
		const b = state.boot;
		$app.innerHTML = `
			<div class="shell">
				<aside class="sidebar">
					<div class="brand"><div class="brand-mark">${icon("shield").replace("<svg", '<svg style="width:18px;height:18px;stroke:#fff;fill:none;stroke-width:2"')}</div>
						<div>Реестр доступа<small>кто есть кто и у кого что</small></div></div>
					<nav class="nav">
						${[...NAV, ...(b.can.admin ? [["lock", "#/app-access", "Доступ к приложению"]] : [])].filter(([, href]) =>
							href === "#/reviews" ? b.can.reviewer || b.pending_reviews || canSee("reviews") || !b.can.read : href === "#/app-access" ? b.can.admin : canSee(NAV_SECTION[href])).map(
							([ic, href, label, badge]) =>
								`<a href="${href}" data-nav="${href}">${icon(ic)}<span>${label}</span>${badge ? `<span class="count" data-badge="${badge}" hidden></span>` : ""}</a>`
						).join("")}
					</nav>
					<div class="sidebar-foot">
						<div class="user"><span class="avatar">${esc(initials(b.user.full_name))}</span><div><b>${esc(b.user.full_name)}</b><span class="muted small">${esc(
							b.can.admin ? "администратор" : (b.can.via || []).join(", ") || "свои задания пересмотра"
						)}</span></div></div>
						<div class="links">${DESK || b.can.roles || b.can.processes ? `<a href="/app/access-registry">Рабочее пространство</a>` : ""}<a href="/?cmd=web_logout">Выйти</a></div>
					</div>
				</aside>
				<div class="main">
					<header class="topbar">
						<button class="icon-btn menu-toggle" aria-label="Меню">${icon("menu")}</button>
						<div class="search" ${b.can.search ? "" : "hidden"}>
							<svg class="icon" viewBox="0 0 24 24">${ICONS.search}</svg>
							<input type="search" placeholder="Сотрудник, учётка, логин, роль, процесс…" autocomplete="off" aria-label="Поиск">
							<kbd>/</kbd>
							<div class="results"></div>
						</div>
						<div class="top-actions">
							<button class="icon-btn theme" title="Тема">${icon("moon")}</button>
						</div>
					</header>
					<div class="view" id="view"></div>
				</div>
			</div>`;
		$app.querySelector(".theme").addEventListener("click", toggleTheme);
		setReviewBadge(b.pending_reviews);
		$app.querySelector(".menu-toggle").addEventListener("click", () => $app.querySelector(".shell").classList.toggle("nav-open"));
		bindSearch();
	}

	function setReviewBadge(n) {
		const badge = $app.querySelector('[data-badge="reviews"]');
		if (!badge) return;
		badge.hidden = !n;
		badge.textContent = n;
		badge.style.background = "var(--accent-soft)";
		badge.style.color = "var(--accent-text)";
	}

	function toggleTheme() {
		const root = document.documentElement;
		const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
		root.dataset.theme = dark ? "light" : "dark";
		storage("registry-theme", root.dataset.theme);
	}

	function bindSearch() {
		const input = $app.querySelector(".search input");
		const box = $app.querySelector(".results");
		let items = [], sel = 0, timer;
		const kindLabel = { person: "сотрудник", account: "учётка", role: "роль", process: "процесс", entitlement: "право" };
		const hrefOf = (r) =>
			r.kind === "person" ? `#/person/${enc(r.id)}` : r.kind === "role" ? `#/role/${enc(r.id)}` : r.kind === "process" ? `#/process/${enc(r.id)}`
				: r.kind === "entitlement" ? `#/entitlement/${enc(r.id)}` : DESK ? deskUrl(r.doctype, r.id) : null;
		const drawResults = () => {
			box.innerHTML = items.length
				? items
						.map(
							(r, i) =>
								`<a class="result ${i === sel ? "sel" : ""}" data-i="${i}" ${hrefOf(r) ? `href="${hrefOf(r)}"` : ""} ${r.kind === "account" && DESK ? 'target="_blank"' : ""}>
									<span class="avatar ${r.kind === "person" ? "" : "gray"}">${esc(initials(r.title))}</span>
									<span><b>${esc(r.title)}</b><small>${esc(r.subtitle || "")}</small></span><span class="kind">${kindLabel[r.kind] || ""}</span></a>`
						)
						.join("")
				: `<div class="empty small">Ничего не найдено</div>`;
			box.classList.add("open");
		};
		input.addEventListener("input", () => {
			clearTimeout(timer);
			const q = input.value.trim();
			if (q.length < 2) return box.classList.remove("open");
			timer = setTimeout(async () => {
				items = await api("search", { query: q }).catch(() => []);
				sel = 0;
				drawResults();
			}, 220);
		});
		input.addEventListener("keydown", (e) => {
			if (!box.classList.contains("open")) return;
			if (e.key === "ArrowDown") (sel = Math.min(sel + 1, items.length - 1)), drawResults(), e.preventDefault();
			if (e.key === "ArrowUp") (sel = Math.max(sel - 1, 0)), drawResults(), e.preventDefault();
			if (e.key === "Enter" && items[sel]) {
				const href = hrefOf(items[sel]);
				if (href) href.startsWith("#") ? (location.hash = href) : window.open(href, "_blank");
				box.classList.remove("open");
				input.value = "";
			}
			if (e.key === "Escape") box.classList.remove("open");
		});
		box.addEventListener("click", () => {
			box.classList.remove("open");
			input.value = "";
		});
		document.addEventListener("click", (e) => {
			if (!e.target.closest(".search")) box.classList.remove("open");
		});
		document.addEventListener("keydown", (e) => {
			if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
				e.preventDefault();
				input.focus();
			}
		});
	}

	// ------------------------------------------------------------------ router

	const ROUTES = [
		[/^\/?$/, viewDashboard],
		[/^\/management$/, viewManagement],
		[/^\/people$/, viewPeople],
		[/^\/person\/(.+)$/, viewPerson],
		[/^\/control(?:\/([a-z]+))?$/, viewControl],
		[/^\/access$/, viewAccess],
		[/^\/entitlement\/(.+)$/, viewEntitlement],
		[/^\/roles$/, viewRoles],
		[/^\/role\/(.+)$/, viewRole],
		[/^\/processes$/, viewProcesses],
		[/^\/process\/(.+)$/, viewProcess],
		[/^\/sources$/, viewSources],
		[/^\/reviews$/, viewReviews],
		[/^\/review\/(.+)$/, viewReview],
		[/^\/app-access$/, viewAppAccess],
		[/^\/reports$/, viewReports],
		[/^\/report\/(.+)$/, viewReport],
	];

	async function route() {
		let path = decodeURIComponent((location.hash || "#/").slice(1)) || "/";
		if (!state.boot.can.read && !path.startsWith("/review")) path = "/reviews";
		else if (path === "/" && !canSee("overview")) path = firstPage();
		const view = document.getElementById("view");
		view.classList.toggle("wide", /^\/(report\/|control|people|access$)/.test(path));
		$app.querySelector(".shell").classList.remove("nav-open");
		$app.querySelectorAll("[data-nav]").forEach((a) => {
			const href = a.dataset.nav.slice(1);
			a.classList.toggle("active", href === "/" ? path === "/" : path.startsWith(href) || (href === "/people" && path.startsWith("/person")) ||
				(href === "/access" && path.startsWith("/entitlement")) || (href === "/roles" && path.startsWith("/role/")) || (href === "/processes" && path.startsWith("/process/")) || (href === "/reviews" && path.startsWith("/review/")) || (href === "/reports" && path.startsWith("/report/")));
		});
		for (const [re, fn] of ROUTES) {
			const m = path.match(re);
			if (m) {
				view.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
				window.scrollTo(0, 0);
				try {
					await fn(view, ...m.slice(1).map((x) => (x === undefined ? x : decodeURIComponent(x))));
				} catch (e) {
					view.innerHTML = `<div class="card error-box">Не удалось загрузить: ${esc(e.message)}</div>`;
				}
				return;
			}
		}
		view.innerHTML = `<div class="card empty"><b>Страница не найдена</b><a href="#/">На главную</a></div>`;
	}

	// ------------------------------------------------------------------ dashboard

	async function loadDashboard(refresh) {
		state.dashboard = await api("dashboard", refresh ? { refresh: 1 } : {});
		const d = state.dashboard;
		const alarms = (d.dismissed_access ? d.dismissed_access.people : 0) + (d.sod || 0);
		const badge = $app.querySelector('[data-badge="control"]');
		if (badge) {
			badge.hidden = !alarms;
			badge.textContent = alarms;
		}
		return d;
	}

	/* a logical section of a page: number, title, one line of explanation, content */
	function block(num, title, lead, content, actions) {
		return `<section class="section"><div class="section-head">${num ? `<span class="num">${String(num).padStart(2, "0")}</span>` : ""}
			<div class="titles"><h2>${title}</h2>${lead ? `<p>${lead}</p>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>${content}</section>`;
	}

	function kpi({ href, label, value, hint, tone, extra, display }) {
		const tag = href ? "a" : "div";
		const toneClass = display !== undefined ? tone || "" : value ? tone || "" : "tone-green";
		return `<${tag} class="card kpi ${toneClass}" ${href ? `href="${href}"` : ""}>
			<div class="label">${esc(label)}</div><div class="value">${display !== undefined ? esc(display) : fmtNum(value)}</div>
			<div class="hint">${hint || ""}</div>${extra || ""}</${tag}>`;
	}

	async function viewDashboard(view, refresh) {
		const d = await loadDashboard(refresh);
		const b = state.boot;
		const r = d.reconciliation;
		const total = r.ok + r.missing + r.excess + r.excess_not_working + r.exceptions;
		const share = (n) => (total ? (100 * n) / total : 0);
		const bySystem = Object.entries(d.dismissed_access.by_system).map(([s, n]) => `<span>${systemBadge(s)} ${n}</span>`).join("");
		const unlinkedTotal = Object.values(d.unlinked).reduce((a, x) => a + x, 0);
		const hour = new Date().getHours();
		const greet = hour < 6 ? "Доброй ночи" : hour < 12 ? "Доброе утро" : hour < 18 ? "Добрый день" : "Добрый вечер";
		view.innerHTML = `
			<div class="hero">
				<div><h1>${greet}, ${esc(b.user.full_name.split(" ")[0])}</h1>
					<p>${fmtNum(d.people.working)} ${plural(d.people.working, "сотрудник работает", "сотрудника работают", "сотрудников работают")} ·
					данные на ${esc(fmtDateTime(d.generated))}</p></div>
				<div style="display:flex;gap:8px"><button class="btn refresh">${icon("refresh")} Обновить</button></div>
			</div>
			${block(1, "Безопасность", "Что нужно закрыть в первую очередь: доступ у уволенных, ничьи учётки, несовместимые права и администраторы.", `<div class="grid grid-4">
				${kpi({ href: "#/control/dismissed", label: "Доступ у неработающих", value: d.dismissed_access.people, tone: "tone-red",
					hint: d.dismissed_access.people ? "уволены, но учётки активны" : "у уволенных нет активных учёток",
					extra: bySystem ? `<div class="split-line">${bySystem}</div>` : "" })}
				${kpi({ href: "#/control/unlinked", label: "Учётки без сотрудника", value: unlinkedTotal, tone: "tone-amber",
					hint: "активные, владелец не найден",
					extra: `<div class="split-line">${Object.entries(d.unlinked).map(([s, n]) => `<span>${systemBadge(s)} ${n}</span>`).join("")}</div>` })}
				${kpi({ href: "#/control/sod", label: "Конфликты полномочий", value: d.sod, tone: "tone-red", hint: "права, которые нельзя совмещать" })}
				${kpi({ href: "#/control/privileged", label: "Привилегированный доступ", value: r.privileged + d.quality.extra_roles + d.quality.b24_admins, tone: "tone-violet",
					hint: `администраторы, роли 1С в обход профилей: ${d.quality.extra_roles}` })}
			</div>`)}
			${block(2, "Положено и есть", "Сверка ролевой модели с тем, что реально выдано в системах.", r.enabled ? `<div class="grid grid-4">
					${kpi({ href: "#/control/excess", label: "Лишние доступы", value: r.excess + r.excess_not_working, tone: "tone-red", hint: `из них у неработающих: ${r.excess_not_working}` })}
					${kpi({ href: "#/control/missing", label: "Не хватает доступов", value: r.missing, tone: "tone-amber", hint: "положены по роли, но не выданы" })}
					${kpi({ href: "#/control/exceptions", label: "Исключения", value: r.exceptions, tone: "tone-violet", hint: d.expiring ? `истекают в ближайшие 2 недели: ${d.expiring}` : "согласованные отступления" })}
					<a class="card kpi" href="#/roles"><div class="label">Соответствие модели</div>
						<div class="value">${total ? Math.round(share(r.ok)) : 0}%</div>
						<div class="meter" title="соответствует / не хватает / лишнее / исключения">
							<i style="width:${share(r.ok)}%;background:var(--green)"></i><i style="width:${share(r.missing)}%;background:var(--amber)"></i>
							<i style="width:${share(r.excess + r.excess_not_working)}%;background:var(--red)"></i><i style="width:${share(r.exceptions)}%;background:var(--border-strong)"></i></div>
						<div class="hint" style="margin-top:10px">${d.roles.active} ${plural(d.roles.active, "роль", "роли", "ролей")} · ${d.roles.entitlements} прав в каталоге${d.roles.drafts ? ` · черновиков: ${d.roles.drafts}` : ""}</div></a>
				</div>` : `<div class="card card-pad"><b>Ролевая модель ещё не настроена.</b>
					<p class="muted" style="margin-top:6px">Опишите права доступа и роли — реестр начнёт показывать, чего не хватает и что лишнее.
					Начать проще с отчёта «Подбор ролей»: он предложит роли по уже выданным доступам.</p>
					${b.can.roles ? `<p style="margin-top:16px"><a class="btn" href="/app/query-report/Role Mining">Подобрать роли</a></p>` : ""}</div>`)}
			${block(3, "Процессы и качество данных", "Кто подменит ключевых людей и совпадают ли системы с кадрами.", `<div class="grid grid-3">
					${kpi({ href: "#/control/processes", label: "Риски процессов", value: d.processes.risks, tone: "tone-amber",
						hint: `${d.processes.total} ${plural(d.processes.total, "процесс", "процесса", "процессов")}, ролей: ${d.processes.roles}` })}
					${kpi({ href: "#/control/quality", label: "Расхождения с кадрами", display: "→", hint: "профили Битрикс24, руководители, employeeNumber в AD" })}
					${kpi({ href: DESK ? "/app/person-merge-candidate?status=Открыт" : null, label: "Кандидаты на склейку", value: d.quality.merge_candidates, tone: "tone-amber", hint: "один человек в разных базах ЗУП" })}
				</div>`)}
			${block(4, "Источники", "Откуда реестр берёт данные и когда загружал их в последний раз.", `<div class="card list">${sourcesList(d.sources.slice(0, 8))}</div>`,
				`<a class="btn small" href="#/sources">Все источники</a>`)}`;
				view.querySelector(".refresh").addEventListener("click", () => viewDashboard(view, true));
	}

	function sourcesList(sources) {
		return (
			sources
				.map(
					(s) => `<div class="list-item"><span class="dot ${s.state}"></span><div class="grow"><b>${esc(s.name)}</b> ${systemBadge(s.kind)}
						<small>${esc(s.title || "")}${s.enabled ? "" : " · выключен"}</small></div>
						<span class="muted small nowrap">${s.last ? esc(fmtDateTime(s.last)) : "не загружалось"}</span></div>`
				)
				.join("") || `<div class="empty">Источники не подключены</div>`
		);
	}

	// ------------------------------------------------------------------ management dashboard

	/* Charts are drawn in SVG by hand: one hue (--chart), hairline grid, a crosshair with a tooltip
	   on hover and on the arrow keys, and a table of all figures as the accessible twin. */
	const MGMT_METRICS = [
		{ key: "dismissed_people", title: "Доступ у неработающих", unit: "чел.", lower: true, href: "#/control/dismissed", hint: "уволены, но учётки активны" },
		{ key: "unlinked", title: "Учётки без сотрудника", lower: true, href: "#/control/unlinked", hint: "активные, владелец не найден" },
		{ key: "excess", title: "Лишние доступы", lower: true, href: "#/control/excess", hint: "выданы сверх ролевой модели", model: true },
		{ key: "sod", title: "Конфликты полномочий", lower: true, href: "#/control/sod", hint: "права, которые нельзя совмещать" },
		{ key: "compliance", title: "Соответствие ролевой модели", percent: true, href: "#/roles", hint: "доступы, совпадающие с моделью", model: true },
		{ key: "coverage", title: "Учётки привязаны к сотрудникам", percent: true, hint: "активные учётки 1С, AD, Битрикс24" },
		{ key: "process_risks", title: "Риски бизнес-процессов", lower: true, href: "#/control/processes", hint: "роли без участников и заместителей" },
		{ key: "events", title: "Кадровые события в работе", lower: true, href: "#/control/events", hint: "приёмы и увольнения: доступы не обработаны" },
		{ key: "suppressed", title: "Погашенные замечания", hint: "приняты с объяснением, в журнале гашений" },
		{ key: "missing", title: "Не хватает доступов", lower: true, href: "#/control/missing", hint: "положены по роли, но не выданы", model: true },
	];
	const MGMT_SYSTEMS = [["1c", "1С"], ["ad", "Active Directory"], ["b24", "Битрикс24"]];
	const PERIOD_TITLES = { 7: "7 дней", 30: "30 дней", 90: "90 дней", 365: "год" };

	const canOpen = (href) =>
		!!href && (href.startsWith("#/control/") ? (state.boot.can.control_lists || []).includes(href.slice(10)) : canSee(NAV_SECTION[href]));

	function withCoverage(p) {
		const total = MGMT_SYSTEMS.reduce((a, [c]) => a + (p[`accounts_${c}`] || 0), 0);
		const linked = MGMT_SYSTEMS.reduce((a, [c]) => a + (p[`linked_${c}`] || 0), 0);
		return { ...p, coverage: total ? Math.round((1000 * linked) / total) / 10 : null };
	}

	const fmtValue = (m, v) => (v === null || v === undefined ? "—" : m.percent ? `${String(v).replace(".", ",")}%` : fmtNum(v));
	const fmtShort = (date) => {
		const d = parseDate(date);
		return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : "";
	};

	function niceMax(v) {
		if (v <= 4) return 4;
		const p = Math.pow(10, Math.floor(Math.log10(v)));
		const n = v / p;
		return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
	}

	function delta(m, points, days) {
		const series = points.filter((p) => p[m.key] !== null && p[m.key] !== undefined);
		if (series.length < 2) return { text: "динамика появится со следующего дня", cls: "" };
		const first = series[0][m.key], last = series[series.length - 1][m.key];
		const diff = Math.round((last - first) * 10) / 10;
		const since = series.length < points.length || daysAgo(series[0].date) < days - 1 ? `с ${fmtShort(series[0].date)}` : `за ${PERIOD_TITLES[days]}`;
		if (!diff) return { text: `без изменений ${since}`, cls: "" };
		const better = m.lower ? diff < 0 : m.percent ? diff > 0 : null;
		const abs = m.percent ? `${String(Math.abs(diff)).replace(".", ",")} п.п.` : fmtNum(Math.abs(diff));
		return { text: `${diff > 0 ? "▲ +" : "▼ −"}${abs} ${since}`, cls: better === null ? "" : better ? "better" : "worse" };
	}

	/* line chart (or sparkline) of one figure into el; redrawn on resize */
	function lineChart(el, points, m, { spark = false, height = 150 } = {}) {
		const draw = () => {
			const series = points.map((p, i) => ({ i, date: p.date, v: p[m.key] })).filter((p) => p.v !== null && p.v !== undefined);
			const W = Math.max(el.clientWidth, 120), H = height;
			const gap = spark ? { l: 2, r: 6, t: 6, b: 4 } : { l: 40, r: 14, t: 10, b: 26 };
			if (!series.length) {
				el.innerHTML = spark ? "" : `<div class="chart-empty">Нет данных</div>`;
				return;
			}
			const values = series.map((p) => p.v);
			let lo = 0, hi = niceMax(Math.max(...values, 1));
			if (m.percent) {
				hi = 100;
				lo = Math.max(0, Math.floor((Math.min(...values) - 5) / 10) * 10);
				if (lo >= 100) lo = 90;
			}
			const t = (d) => parseDate(d).getTime();
			const t0 = t(points[0].date), t1 = t(points[points.length - 1].date);
			const x = (d) => (t1 === t0 ? (gap.l + W - gap.r) / 2 : gap.l + ((t(d) - t0) / (t1 - t0)) * (W - gap.l - gap.r));
			const y = (v) => gap.t + (1 - (v - lo) / (hi - lo)) * (H - gap.t - gap.b);
			const line = series.map((p, k) => `${k ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
			const area = series.length > 1 ? `${line}L${x(series[series.length - 1].date).toFixed(1)},${y(lo)}L${x(series[0].date).toFixed(1)},${y(lo)}Z` : "";
			const last = series[series.length - 1];
			let grid = "";
			if (!spark) {
				const ticks = [lo, lo + (hi - lo) / 2, hi];
				grid = ticks.map((v) => `<line class="grid" x1="${gap.l}" x2="${W - gap.r}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${gap.l - 8}" y="${y(v) + 4}" text-anchor="end">${m.percent ? v + "%" : fmtNum(v)}</text>`).join("");
				const labels = [points[0]];
				if (W > 420 && points.length > 2) labels.push(points[Math.floor((points.length - 1) / 2)]);
				if (points.length > 1) labels.push(points[points.length - 1]);
				grid += labels.map((p, k) => `<text class="tick" x="${x(p.date)}" y="${H - 6}" text-anchor="${k === 0 && labels.length > 1 ? "start" : k === labels.length - 1 && labels.length > 1 ? "end" : "middle"}">${fmtShort(p.date)}</text>`).join("");
			}
			el.innerHTML = `<svg width="${W}" height="${H}" role="img" aria-label="${esc(m.title)}: ${esc(fmtValue(m, last.v))}">
				${grid}${area ? `<path class="area" d="${area}"/>` : ""}<path class="line" d="${line}"/>
				<circle class="end" cx="${x(last.date)}" cy="${y(last.v)}" r="${spark ? 3 : 4}"/>
				${spark ? "" : `<line class="cross" y1="${gap.t}" y2="${H - gap.b}" visibility="hidden"/><circle class="hover" r="4" visibility="hidden"/><rect class="hit" x="0" y="0" width="${W}" height="${H}"/>`}
			</svg>${spark ? "" : `<div class="tip" hidden></div>`}`;
			if (spark) return;
			const svg = el.querySelector("svg"), tip = el.querySelector(".tip");
			const cross = svg.querySelector(".cross"), dot = svg.querySelector(".hover");
			let current = series.length - 1;
			const show = (k) => {
				current = Math.max(0, Math.min(series.length - 1, k));
				const p = series[current], px = x(p.date), py = y(p.v);
				cross.setAttribute("x1", px);
				cross.setAttribute("x2", px);
				dot.setAttribute("cx", px);
				dot.setAttribute("cy", py);
				[cross, dot].forEach((n) => n.setAttribute("visibility", "visible"));
				tip.hidden = false;
				tip.innerHTML = `<small>${esc(fmtDate(p.date))}</small><b>${esc(fmtValue(m, p.v))}</b>`;
				const left = Math.min(Math.max(px - tip.offsetWidth / 2, 0), W - tip.offsetWidth);
				tip.style.left = `${left}px`;
				tip.style.top = `${Math.max(py - tip.offsetHeight - 12, 0)}px`;
			};
			const hide = () => {
				[cross, dot].forEach((n) => n.setAttribute("visibility", "hidden"));
				tip.hidden = true;
			};
			const nearest = (clientX) => {
				const px = clientX - svg.getBoundingClientRect().left;
				let best = 0;
				series.forEach((p, k) => Math.abs(x(p.date) - px) < Math.abs(x(series[best].date) - px) && (best = k));
				return best;
			};
			svg.addEventListener("mousemove", (e) => show(nearest(e.clientX)));
			svg.addEventListener("mouseleave", hide);
			el.onkeydown = (e) => {
				if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
					e.preventDefault();
					show(current + (e.key === "ArrowLeft" ? -1 : 1));
				}
			};
			el.onfocus = () => show(current);
			el.onblur = hide;
		};
		draw();
		if (window.ResizeObserver) {
			let width = el.clientWidth;
			new ResizeObserver(() => el.clientWidth !== width && ((width = el.clientWidth), draw())).observe(el);
		}
	}

	/* horizontal bars of one figure by category: one hue, the value at the end of the bar */
	function bars(items, { percent = false } = {}) {
		const max = Math.max(...items.map((i) => i.value || 0), 1);
		return `<div class="hbars">${items
			.map((i) => {
				const w = percent ? i.value || 0 : (100 * (i.value || 0)) / max;
				const tag = i.href ? "a" : "div";
				return `<${tag} class="hbar" ${i.href ? `href="${i.href}"` : ""} title="${esc(i.label)}: ${esc(i.text || fmtNum(i.value))}">
					<span class="hbar-label">${esc(i.label)}${i.sub ? `<small>${esc(i.sub)}</small>` : ""}</span>
					<span class="hbar-track"><i style="width:${Math.max(w, i.value ? 1.5 : 0)}%"></i></span>
					<span class="hbar-value">${esc(i.text || fmtNum(i.value))}</span></${tag}>`;
			})
			.join("")}</div>`;
	}

	async function viewManagement(view, refresh) {
		const days = +(storage("registry-mgmt-days") || 30);
		const d = await api("management", { days, refresh: refresh ? 1 : 0 });
		const points = d.history.map(withCoverage);
		const now = points[points.length - 1];
		const metrics = MGMT_METRICS.filter((m) => !m.model || now.model_enabled);
		const tiles = metrics.slice(0, 6);
		const trends = metrics.filter((m) => !["coverage", "compliance"].includes(m.key) || points.length > 1);
		const tile = (m, i) => {
			const dl = delta(m, points, d.days);
			const tag = canOpen(m.href) ? "a" : "div";
			return `<${tag} class="card kpi mtile" ${tag === "a" ? `href="${m.href}"` : ""}>
				<div class="label">${esc(m.title)}</div>
				<div class="value">${esc(fmtValue(m, now[m.key]))}</div>
				<div class="delta ${dl.cls}">${esc(dl.text)}</div>
				<div class="spark" data-spark="${i}" aria-hidden="true"></div>
				<div class="hint">${esc(m.hint)}</div></${tag}>`;
		};
		const systemsDismissed = MGMT_SYSTEMS.map(([c, label]) => ({ label, value: now[`dismissed_${c}`] || 0 }));
		const systemsUnlinked = MGMT_SYSTEMS.map(([c, label]) => ({ label, value: now[`unlinked_${c}`] || 0 }));
		const coverage = MGMT_SYSTEMS.filter(([c]) => now[`accounts_${c}`]).map(([c, label]) => {
			const total = now[`accounts_${c}`], linked = now[`linked_${c}`];
			const share = Math.round((1000 * linked) / total) / 10;
			return { label, value: share, text: `${String(share).replace(".", ",")}%`, sub: `${fmtNum(linked)} из ${fmtNum(total)}` };
		});
		const checked = now.ok + now.excess + now.missing;
		const model = now.model_enabled
			? `<div class="grid grid-2">
				<div class="card card-pad"><div class="group-title">Соответствие модели</div>
					<div class="hero-num">${esc(fmtValue({ percent: true }, now.compliance))}</div>
					<div class="meter big" role="img" aria-label="соответствует ${now.ok} из ${checked}"><i style="width:${checked ? (100 * now.ok) / checked : 0}%;background:var(--chart)"></i></div>
					<p class="muted small" style="margin-top:12px">${fmtNum(now.ok)} из ${fmtNum(checked)} доступов совпадают с тем, что положено по ролям и процессам.
					Остальное — лишние доступы или недостающие.</p></div>
				<div class="card card-pad"><div class="group-title">Из чего складывается</div>${bars([
					{ label: "Соответствует модели", value: now.ok },
					{ label: "Лишние доступы", value: now.excess, sub: now.excess_not_working ? `у неработающих: ${fmtNum(now.excess_not_working)}` : "" },
					{ label: "Не хватает доступов", value: now.missing },
					{ label: "Согласованные исключения", value: now.exceptions, sub: now.expiring ? `истекают за 2 недели: ${fmtNum(now.expiring)}` : "" },
					{ label: "Привилегированный доступ", value: now.privileged + (now.extra_roles || 0) + (now.b24_admins || 0) },
				])}</div></div>`
			: `<div class="card card-pad"><b>Ролевая модель ещё не настроена.</b><p class="muted" style="margin-top:6px">Когда роли доступа будут описаны, здесь появится доля доступов,
				совпадающих с моделью, и сколько выдано лишнего.</p></div>`;
		const reviews = d.reviews.length
			? `<div class="card list">${d.reviews
					.map((r) => {
						const share = r.items_total ? Math.round((100 * r.items_done) / r.items_total) : 0;
						return `<div class="list-item review-progress"><div class="grow"><b>${esc(r.title || r.name)}</b>
							<small>${r.status === "Идёт" ? `идёт${r.due_date ? `, срок ${esc(fmtDate(r.due_date))}` : ""}` : `завершён ${esc(fmtDate(r.finished_on))}`} ·
							решений ${fmtNum(r.items_done)} из ${fmtNum(r.items_total)} · отозвать: ${fmtNum(r.items_revoke)}</small></div>
							<div class="meter" role="img" aria-label="${share}%"><i style="width:${share}%;background:var(--chart)"></i></div><b class="nowrap">${share}%</b></div>`;
					})
					.join("")}</div>`
			: `<div class="card empty"><b>Кампаний пересмотра за 90 дней не было</b>Пересмотр — это когда руководители подтверждают или отзывают доступы своих сотрудников.</div>`;
		view.innerHTML = `
			<div class="page-head"><div><h1>Руководству</h1><p>Итоги по доступам и как они меняются. Только цифры, без списков людей и учёток.
				Данные на ${esc(fmtDateTime(d.generated))}.</p></div>
				<div class="mgmt-tools"><div class="subnav period">${d.periods.map((p) => `<button class="chip ${p === d.days ? "on" : ""}" data-days="${p}">${PERIOD_TITLES[p]}</button>`).join("")}</div>
				<button class="btn small refresh">${icon("refresh")} Обновить</button><button class="btn small print">Печать</button></div></div>
			${block(1, "Главное", `${fmtNum(now.people_working)} ${plural(now.people_working, "сотрудник работает", "сотрудника работают", "сотрудников работают")}. Под цифрой — изменение за выбранный период.`,
				`<div class="grid grid-3">${tiles.map(tile).join("")}</div>`)}
			${block(2, "Динамика", points.length > 1 ? "Как менялись показатели день за днём. Наведите на график — покажет значение на дату." :
				"Реестр сохраняет показатели каждый час; график по дням появится со следующего дня.",
				`<div class="grid grid-3">${trends.map((m, i) => `<div class="card card-pad chart-card"><div class="chart-head"><span>${esc(m.title)}</span><b>${esc(fmtValue(m, now[m.key]))}</b></div>
					<div class="chart" data-chart="${i}" tabindex="0"></div></div>`).join("")}</div>
				<div class="card mgmt-table" style="margin-top:16px" hidden></div>`,
				`<button class="btn small as-table">Таблицей</button>`)}
			${block(3, "По системам", "Где проблемы: 1С, Active Directory, Битрикс24.", `<div class="grid grid-3">
				<div class="card card-pad"><div class="group-title">Доступ у неработающих, чел.</div>${bars(systemsDismissed)}</div>
				<div class="card card-pad"><div class="group-title">Учётки без сотрудника</div>${bars(systemsUnlinked)}</div>
				<div class="card card-pad"><div class="group-title">Учётки привязаны к сотрудникам</div>${coverage.length ? bars(coverage, { percent: true }) : `<p class="muted">Учёток пока нет</p>`}</div></div>`)}
			${block(4, "Ролевая модель", "Насколько выданные доступы совпадают с тем, что положено по должности и процессам.", model)}
			${block(5, "Пересмотр доступа", "Кампании, где руководители подтверждают доступы сотрудников.", reviews)}
			${block(6, "Источники данных", "Свежесть данных: когда реестр последний раз загрузил каждый источник.", `<div class="card list">${sourcesList(d.sources)}</div>`)}`;
		view.querySelectorAll("[data-spark]").forEach((el) => lineChart(el, points, tiles[+el.dataset.spark], { spark: true, height: 36 }));
		view.querySelectorAll("[data-chart]").forEach((el) => lineChart(el, points, trends[+el.dataset.chart]));
		view.querySelectorAll("[data-days]").forEach((b) =>
			b.addEventListener("click", () => {
				storage("registry-mgmt-days", b.dataset.days);
				viewManagement(view);
			})
		);
		view.querySelector(".refresh").addEventListener("click", () => viewManagement(view, true));
		view.querySelector(".print").addEventListener("click", () => window.print());
		const cols = metrics.filter((m) => m.key !== "coverage" || points.some((p) => p.coverage !== null));
		table(view.querySelector(".mgmt-table"), {
			name: "показатели-реестра",
			filter: false,
			rows: [...points].reverse(),
			columns: [{ key: "date", label: "Дата", type: "date" }, ...cols.map((m) => ({ key: m.key, label: m.title + (m.percent ? ", %" : ""), type: "number" }))],
		});
		view.querySelector(".as-table").addEventListener("click", (e) => {
			const box = view.querySelector(".mgmt-table");
			box.hidden = !box.hidden;
			e.target.textContent = box.hidden ? "Таблицей" : "Скрыть таблицу";
		});
	}

	// ------------------------------------------------------------------ people

	const FLAGS = {
		dismissed_access: ["Уволен, доступ есть", "t-red"],
		no_access: ["Нет учёток", ""],
		extra_roles: ["Роли 1С в обход профилей", "t-amber"],
		b24_admin: ["Админ Битрикс24", "t-violet"],
	};

	async function viewPeople(view) {
		const f = state.peopleFilters;
		const orgs = await api("organizations");
		view.innerHTML = `
			<div class="page-head"><div><h1>Сотрудники</h1><p>Люди из кадров ЗУП и их учётные записи в 1С, Active Directory и Битрикс24.</p></div></div>
			<div class="toolbar">
				<input class="field q" placeholder="ФИО" value="${esc(f.query)}" style="min-width:240px">
				<select class="field org"><option value="">Все организации</option>${orgs.map((o) => `<option value="${esc(o.name)}" ${o.name === f.organization ? "selected" : ""}>${esc(o.title)}</option>`).join("")}</select>
				<select class="field st">${["", "Работает", "Уволен", "Не принят", "Нет в выгрузке"].map((s) => `<option value="${s}" ${s === f.status ? "selected" : ""}>${s || "Любой статус"}</option>`).join("")}</select>
				<div class="chips">${Object.entries(FLAGS).map(([k, [label]]) => `<button class="chip ${f.flag === k ? "on" : ""}" data-flag="${k}">${label}</button>`).join("")}</div>
			</div>
			<div class="card people-table"></div>`;
		const load = async () => {
			const box = view.querySelector(".people-table");
			box.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
			const data = await api("people", { ...f, limit: 500 });
			table(box, {
				name: "сотрудники",
				rows: data.rows,
				empty: "Никого не нашли",
				columns: [
					{ key: "full_name", label: "Сотрудник", type: "person", render: (r) => cell({ key: "full_name", type: "person" }, { ...r, person: r.name }) },
					{ key: "status", label: "Статус", type: "status" },
					{ key: "position", label: "Должность" },
					{ key: "department", label: "Подразделение" },
					{ key: "organization", label: "Организация" },
					{
						key: "systems",
						label: "Учётки",
						render: (r) => `<span class="sys"><span class="${r.ib ? "on" : ""}">1С${r.ib > 1 ? "×" + r.ib : ""}</span><span class="${r.ad ? "on" : ""}">AD</span><span class="${r.b24 ? "on" : ""}">Б24</span></span>`,
						csv: (r) => [r.ib ? `1С:${r.ib}` : "", r.ad ? "AD" : "", r.b24 ? "Б24" : ""].filter(Boolean).join(" "),
					},
					{ key: "flags", label: "Внимание", render: (r) => r.flags.map((x) => pill(FLAGS[x][0], FLAGS[x][1])).join(" "), csv: (r) => r.flags.map((x) => FLAGS[x][0]).join(", ") },
				],
			});
			if (data.total > data.rows.length) box.insertAdjacentHTML("beforeend", `<div class="table-foot">Показаны первые ${data.rows.length} из ${data.total}: уточните поиск</div>`);
		};
		let timer;
		view.querySelector(".q").addEventListener("input", (e) => {
			clearTimeout(timer);
			timer = setTimeout(() => ((f.query = e.target.value.trim()), load()), 300);
		});
		view.querySelector(".org").addEventListener("change", (e) => ((f.organization = e.target.value), load()));
		view.querySelector(".st").addEventListener("change", (e) => ((f.status = e.target.value), load()));
		view.querySelectorAll("[data-flag]").forEach((b) =>
			b.addEventListener("click", () => {
				f.flag = f.flag === b.dataset.flag ? "" : b.dataset.flag;
				if (f.flag === "dismissed_access") f.status = "";
				viewPeople(view);
			})
		);
		await load();
	}

	// ------------------------------------------------------------------ person

	async function viewPerson(view, name) {
		const d = await api("person", { name });
		const p = d.person;
		const can = state.boot.can;
		const activeIb = d.ib.filter((a) => a.login_allowed && !a.invalid);
		const activeAd = d.ad.filter((a) => a.enabled && !a.missing_in_source);
		const activeB24 = d.b24.filter((u) => u.active && !u.missing_in_source);
		const working = p.status === "Работает";
		const recon = d.reconciliation;
		const counts = recon.reduce((acc, r) => ((acc[r.status] = (acc[r.status] || 0) + 1), acc), {});
		const alerts = [];
		if (!working && (activeIb.length || activeAd.length || activeB24.length))
			alerts.push(["red", `Сотрудник не работает (${p.status.toLowerCase()}), но доступ остался: ${[
				activeIb.length && `1С — ${activeIb.length}`, activeAd.length && "учётка AD", activeB24.length && "Битрикс24"].filter(Boolean).join(", ")}.`]);
		d.sod.forEach((s) => alerts.push(["red", `Конфликт полномочий «${s.title}»: ${s.side_a} и ${s.side_b}.`]));
		if (counts["Лишнее"]) alerts.push(["amber", `Лишних доступов: ${counts["Лишнее"]} — не положены ни по роли, ни по процессу.`]);

		view.innerHTML = `
			<div class="crumbs"><a href="#/people">Сотрудники</a> / ${esc(p.full_name)}</div>
			<div class="card profile">
				<span class="avatar lg ${working ? "" : "gray"}">${esc(initials(p.full_name))}</span>
				<div style="flex:1;min-width:240px">
					<h1>${esc(p.full_name)} ${statusPill(p.status)} ${p.presence === "Длительное отсутствие" ? pill("длительное отсутствие", "t-violet") : ""}
						${p.external_part_time_only ? pill("только совместительство", "t-amber") : ""}</h1>
					<div class="meta">${[p.position, p.department, p.organization].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("")}
						${p.birth_date ? `<span>дата рождения ${esc(fmtDate(p.birth_date))}</span>` : ""}</div>
				</div>
				${DESK ? `<a class="btn" href="${deskUrl("Person", p.name)}" target="_blank" rel="noopener">${icon("external")} Карточка</a>` : ""}
			</div>
			${alerts.map(([tone, text]) => `<div class="alert ${tone}">${esc(text)}</div>`).join("")}
			<div class="tabs">
				<button data-tab="access" class="on">Доступы <span class="n">${activeIb.length + activeAd.length + activeB24.length}</span></button>
				<button data-tab="recon">Положено и есть ${counts["Не хватает"] || counts["Лишнее"] || counts["Лишнее: не работает"] ? `<span class="n" style="background:var(--strong);color:var(--on-strong)">${(counts["Не хватает"] || 0) + (counts["Лишнее"] || 0) + (counts["Лишнее: не работает"] || 0)}</span>` : ""}</button>
				<button data-tab="roles">Роли и процессы <span class="n">${d.roles.length + d.process_roles.length}</span></button>
				<button data-tab="hr">Кадры</button>
			</div>
			<div class="tab-body"></div>`;

		const tabs = {
			access: () => personAccess(d),
			recon: () => "",
			roles: () => personRoles(d),
			hr: () => personHr(d),
		};
		const body = view.querySelector(".tab-body");
		const open = (tab) => {
			view.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
			body.innerHTML = tabs[tab]();
			if (tab === "recon") personRecon(body, d, can, () => viewPerson(view, name));
			if (tab === "access") {
				const box = body.querySelector(".b24-access");
				if (box)
					table(box, {
						name: "битрикс24-доступ",
						rows: d.b24_access,
						filter: d.b24_access.length > 8,
						empty: "Доступов к разделам нет",
						columns: [
							{ key: "resource_type", label: "Раздел", type: "badge" },
							{ key: "resource", label: "Ресурс" },
							{ key: "permission", label: "Права" },
							{ key: "via", label: "Через" },
						],
					});
				body.querySelectorAll("[data-panel-btn]").forEach((btn) =>
					btn.addEventListener("click", () => {
						body.querySelectorAll("[data-panel-btn]").forEach((x) => x.classList.toggle("on", x === btn));
						body.querySelectorAll("[data-panel]").forEach((panel) => (panel.hidden = panel.dataset.panel !== btn.dataset.panelBtn));
					})
				);
				const shares = body.querySelector(".shares-access");
				if (shares)
					table(shares, {
						name: "общие-папки",
						rows: d.shares,
						filter: d.shares.length > 8,
						empty: "Доступа к общим папкам нет",
						columns: [
							{ key: "share_name", label: "Общая папка", type: "badge" },
							{ key: "path", label: "Папка" },
							{ key: "level", label: "Доступ" },
							{ key: "via", label: "Через" },
							{ key: "login", label: "Учётка" },
						],
					});
			}
		};
		view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => open(b.dataset.tab)));
		open("access");
	}

	function personAccess(d) {
		const ib = d.ib
			.map((a) => {
				const on = a.login_allowed && !a.invalid;
				return `<div class="card acct ${on ? "" : "off"} ${on && d.person.status !== "Работает" ? "danger" : ""}">
					<div class="acct-head"><b>${esc(a.base_code)}</b>${systemBadge("1С")} ${pill(a.base_configuration, "")}
						<span style="margin-left:auto">${on ? pill("вход разрешён", "t-green") : pill("вход закрыт", "")}</span></div>
					<div><b>${esc(a.user_name)}</b></div>
					<dl class="kv"><dt>Логин</dt><dd>${esc(a.login || "—")}</dd>
						<dt>Логин AD</dt><dd>${esc(a.ad_login || "—")} ${a.ad_state === "off" ? pill("учётка AD отключена", "t-red") : a.ad_state === "missing" ? pill("нет в AD", "t-amber") : ""}</dd>
						<dt>Организации</dt><dd>${esc(a.orgs_text || "—")}</dd>
						<dt>Профили</dt><dd>${a.profiles.map((p) => `<span class="tag" title="${esc((p.restrictions || []).join("\n"))}">${esc(p.profile)}</span>`).join(" ") || "—"}</dd></dl>
					${a.extra_roles.length ? `<div class="tags">${a.extra_roles.map((r) => pill(r, "t-red")).join("")}</div><div class="muted small">роли в обход профилей</div>` : ""}
				</div>`;
			})
			.join("");
		const ad = d.ad
			.map((a) => {
				const on = a.enabled && !a.missing_in_source;
				return `<div class="card acct ${on ? "" : "off"} ${on && d.person.status !== "Работает" ? "danger" : ""}">
					<div class="acct-head"><b>${esc(a.domain)}</b>${systemBadge("AD")}
						<span style="margin-left:auto">${a.missing_in_source ? pill("нет в AD", "t-amber") : on ? pill("включена", "t-green") : pill("отключена", "")}</span></div>
					<div><b>${esc(a.display_name)}</b> <span class="muted">${esc(a.sam_account_name || "")}</span></div>
					<dl class="kv"><dt>Последний вход</dt><dd>${a.last_logon ? esc(fmtDateTime(a.last_logon)) : "никогда"}</dd>
						<dt>Пароль</dt><dd>${a.password_last_set ? "сменён " + esc(fmtDate(a.password_last_set)) : "—"} ${a.password_never_expires ? pill("не истекает", "t-amber") : ""}</dd>
						<dt>OU</dt><dd>${esc(a.ou || "—")}</dd>
						<dt>employeeNumber</dt><dd>${a.employee_number_ok ? pill("совпадает", "t-green") : pill(a.employee_number || "не заполнен", "t-amber")}</dd></dl>
					<div class="tags">${a.groups.map((g) => `<span class="tag">${esc(g.name)}</span>`).join("")}</div>
				</div>`;
			})
			.join("");
		const b24 = d.b24
			.map((u) => {
				const on = u.active && !u.missing_in_source;
				return `<div class="card acct ${on ? "" : "off"} ${on && d.person.status !== "Работает" ? "danger" : ""}">
					<div class="acct-head"><b>${esc(u.portal)}</b>${systemBadge("Битрикс24")} ${u.is_admin ? pill("администратор", "t-violet") : ""}
						<span style="margin-left:auto">${on ? pill("активен", "t-green") : pill("не активен", "")}</span></div>
					<div><b>${esc(u.full_name)}</b> <span class="muted">${esc(u.email || "")}</span></div>
					<dl class="kv"><dt>Должность</dt><dd>${esc(u.work_position || "—")}</dd>
						<dt>Подразделения</dt><dd>${esc((u.departments || []).join(", ") || "—")}</dd>
						<dt>Последний вход</dt><dd>${u.last_login ? esc(fmtDateTime(u.last_login)) : "никогда"}</dd>
						${u.birthday !== undefined ? `<dt>Дата рождения</dt><dd>${u.birthday ? esc(fmtDate(u.birthday)) : "—"}</dd>` : ""}</dl>
					<div class="tags">${(u.workgroups || []).map((g) => `<span class="tag">${esc(g.group_name)} · ${esc(g.role)}</span>`).join("")}</div>
				</div>`;
			})
			.join("");
		const cards = (content, emptyText) => (content ? `<div class="grid grid-3">${content}</div>` : `<div class="card empty">${emptyText}</div>`);
		const risky = (list, isOn) => (d.person.status !== "Работает" ? list.filter(isOn).length : 0);
		// one system at a time: the card does not turn into a long scroll
		const panels = [
			["ib", "1С", d.ib.length, risky(d.ib, (a) => a.login_allowed && !a.invalid),
				`<div class="group"><h3>Учётные записи в базах 1С</h3>${cards(ib, "Учётных записей 1С нет")}</div>`],
			["ad", "Active Directory", d.ad.length, risky(d.ad, (a) => a.enabled && !a.missing_in_source),
				`<div class="group"><h3>Учётки и группы домена</h3>${cards(ad, "Учётки AD нет")}</div>`],
			["b24", "Битрикс24", d.b24.length, risky(d.b24, (u) => u.active && !u.missing_in_source),
				`<div class="group"><h3>Пользователь портала</h3>${cards(b24, "Пользователя Битрикс24 нет")}</div>` +
				(d.b24.length ? `<div class="group"><h3>Доступ к разделам: CRM, смарт-процессы, диск</h3><div class="card b24-access"></div></div>` : "")],
			["shares", "Общие папки", d.shares.length, risky(d.shares, () => true),
				`<div class="group"><h3>Папки Synology, к которым есть доступ</h3>${d.shares.length ? `<div class="card shares-access"></div>` : `<div class="card empty">Доступа к общим папкам нет</div>`}</div>`],
		];
		const first = (panels.find((x) => x[3]) || panels.find((x) => x[2]) || panels[0])[0];
		return `<div class="subnav">${panels
			.map(([key, label, n, alarm]) => `<button class="chip ${key === first ? "on" : ""}" data-panel-btn="${key}">${label} <span class="n ${alarm ? "alarm" : ""}">${n}</span></button>`)
			.join("")}</div>
			${panels.map(([key, , , , html]) => `<section class="section" data-panel="${key}" ${key === first ? "" : "hidden"}>${html}</section>`).join("")}`;
	}

	function personRecon(body, d, can, reload) {
		const rows = d.reconciliation;
		body.innerHTML = `<section class="section"><p class="muted" style="margin-bottom:16px;max-width:760px">Что положено сотруднику по ролям доступа и ролям в процессах, и что у него есть в системах.
			«Лишнее» — есть, но не положено; «Исключение» — согласованное лишнее.</p><div class="card recon"></div></section>`;
		table(body.querySelector(".recon"), {
			name: "сверка-" + d.person.full_name,
			rows,
			empty: state.boot.layers.roles ? "Ничего не положено и ничего не выдано из каталога" : "Ролевая модель ещё не настроена",
			columns: [
				{ key: "status", label: "Итог", type: "recon" },
				{ key: "title", label: "Право доступа", type: "entitlement" },
				{ key: "system", label: "Система", type: "badge" },
				{ key: "risk", label: "Риск", type: "risk" },
				{ key: "expected_by", label: "Положено по" },
				{ key: "evidence", label: "Где есть" },
			],
			actions: can.exceptions
				? (r) =>
						r.status === "Лишнее"
							? `<button class="btn small exc" data-ent="${esc(r.entitlement)}" data-title="${esc(r.title)}">Согласовать</button>`
							: ""
				: null,
		});
		body.querySelectorAll(".exc").forEach((btn) =>
			btn.addEventListener("click", () =>
				modal({
					title: "Согласовать как исключение",
					text: `«${btn.dataset.title}» у сотрудника ${d.person.full_name}. Доступ перестанет считаться лишним до указанной даты.`,
					fields: [
						{ name: "reason", label: "Почему согласовано", type: "textarea", required: true },
						{ name: "valid_to", label: "Действует по (пусто — бессрочно)", type: "date" },
					],
					submit: async (values) => {
						await api("create_exception", { person: d.person.name, entitlement: btn.dataset.ent, ...values }, true);
						toast("Исключение согласовано");
						reload();
					},
				})
			)
		);
	}

	function personRoles(d) {
		const roles = d.roles
			.map((r) => `<a class="list-item" href="#/role/${enc(r.role)}"><span class="avatar gray">${esc(initials(r.role))}</span><div class="grow"><b>${esc(r.role)}</b><small>${esc(r.reason)}</small></div>→</a>`)
			.join("");
		const procs = d.process_roles
			.map((r) => `<a class="list-item" href="#/process/${enc(r.process)}"><div class="grow"><b>${esc(r.role)}</b> ${pill(r.raci, "t-blue")}<small>${esc(r.process_title)} · ${esc(r.how)}</small></div>→</a>`)
			.join("");
		return `<section class="section"><div class="grid grid-2">
			<div class="group"><h3>Роли доступа</h3><div class="card list">${roles || `<div class="empty">Ролей нет</div>`}</div></div>
			<div class="group"><h3>Роли в бизнес-процессах</h3><div class="card list">${procs || `<div class="empty">В процессах не участвует</div>`}</div></div>
		</div></section>`;
	}

	function personHr(d) {
		const emps = d.employments
			.map((e) => `<div class="list-item"><div class="grow"><b>${esc(e.position || "—")}</b> ${statusPill(e.status)}
				<small>${esc([e.department, e.organization].filter(Boolean).join(" · "))}</small>
				<small>${esc(e.employment_kind || "")} · ${esc(e.source)} ${esc(e.tab_number || "")} · ${e.hire_date ? "с " + esc(fmtDate(e.hire_date)) : ""}${e.termination_date ? " по " + esc(fmtDate(e.termination_date)) : ""}</small></div></div>`)
			.join("");
		const abs = d.absences
			.map((a) => `<div class="list-item"><div class="grow"><b>${esc(a.state || a.category)}</b><small>${esc(fmtDate(a.date_from))} — ${esc(fmtDate(a.date_to))}</small></div></div>`)
			.join("");
		const events = d.events
			.map((e) => `<div class="list-item"><div class="grow"><b>${esc(e.event_type)}</b> <span class="muted small">${esc(fmtDate(e.event_date))}</span><small>${esc(e.details || "")}</small></div></div>`)
			.join("");
		return `<section class="section"><div class="grid grid-2">
			<div class="group"><h3>Трудоустройства</h3><div class="card list">${emps || `<div class="empty">Нет</div>`}</div></div>
			<div><div class="group"><h3>Отсутствия</h3><div class="card list">${abs || `<div class="empty">Нет</div>`}</div></div>
			<div class="group"><h3>Кадровые события</h3><div class="card list">${events || `<div class="empty">Нет</div>`}</div></div></div>
		</div></section>`;
	}

	// ------------------------------------------------------------------ control

	const CONTROL_HELP = {
		dismissed: "Сотрудник уволен или пропал из кадров, а учётная запись активна. Первое, что нужно закрыть.",
		unlinked: "Активные учётки, для которых не найден сотрудник: служебные, внешние или найденные неверно. Разберите и привяжите вручную.",
		excess: "Доступ есть, но не положен ни по роли, ни по процессу. Отзовите или согласуйте как исключение.",
		missing: "Доступ положен по роли или процессу, но не выдан. Проверьте, не мешает ли это работе.",
		sod: "У сотрудника есть права, которые по правилам нельзя совмещать.",
		privileged: "Администраторы, полные права, доступ к деньгам и персональным данным. Проверяйте регулярно.",
		exceptions: "Согласованные отступления и роли, выданные вручную. Следите за сроками.",
		stale: "Учётки включены, но ими давно не пользовались (больше 90 дней) или не входили никогда.",
		processes: "Роли процессов без участников, без заместителя или с неработающими участниками.",
		quality: "Данные в Битрикс24 и AD, которые не совпадают с кадрами ЗУП.",
		events: "Необработанные кадровые события: кому после приёма или перевода выдать положенное, у кого после увольнения отключить учётки и отозвать права.",
		shares: "Права на папках Synology: выданные напрямую людям, доступ для всех, запреты, удалённые учётки, локальные учётки NAS, доступ у неработающих.",
		journal: "Все погашенные замечания: что, кто и когда погасил и почему, до какой даты; кто и почему вернул. Записи не удаляются.",
	};
	const CONTROL_ORDER = ["dismissed", "events", "sod", "excess", "privileged", "unlinked", "missing", "exceptions", "stale", "processes", "quality", "shares", "journal"];
	// lists grouped by meaning, so twelve lists do not read as one row of buttons
	const CONTROL_GROUPS = [
		["Закрыть срочно", ["dismissed", "events", "sod", "privileged"]],
		["Положено и выдано", ["excess", "missing", "exceptions"]],
		["Порядок в учётках и данных", ["unlinked", "stale", "quality", "shares", "processes"]],
		["Разобрано", ["journal"]],
	];
	const ALARM_CONTROLS = new Set(["dismissed", "sod", "excess"]);
	const CONTROL_TITLES = {
		dismissed: "Доступ у неработающих", unlinked: "Учётки без сотрудника", excess: "Лишние доступы", missing: "Не хватает доступов",
		sod: "Конфликты полномочий", privileged: "Привилегированный доступ", exceptions: "Исключения и сроки", stale: "Давно не входили",
		processes: "Риски процессов", quality: "Расхождения с кадрами", events: "Кадровые события", shares: "Общие папки", journal: "Журнал гашений",
	};

	async function viewControl(view, kind, showSuppressed) {
		const can = state.boot.can;
		const allowed = can.control_lists || [];
		kind = kind && allowed.includes(kind) ? kind : CONTROL_ORDER.find((k) => allowed.includes(k)) || kind || "dismissed";
		const d = state.dashboard || (await loadDashboard());
		const r = d.reconciliation;
		const counts = {
			dismissed: d.dismissed_access ? d.dismissed_access.people : undefined,
			unlinked: d.unlinked ? Object.values(d.unlinked).reduce((a, x) => a + x, 0) : undefined,
			excess: r ? r.excess + r.excess_not_working : undefined,
			missing: r ? r.missing : undefined,
			sod: d.sod,
			exceptions: r ? r.exceptions : undefined,
			processes: d.processes ? d.processes.risks : undefined,
			events: d.events,
			journal: d.suppressed,
		};
		view.innerHTML = `
			<div class="page-head"><div><h1>Контроль</h1><p>Что требует решения: списки для службы безопасности, ИБ и контролёров прав. Каждый список можно выгрузить в CSV.
				Замечание, которое разобрали и приняли (например, учётка подрядчика без сотрудника), можно погасить с комментарием — оно попадёт в журнал.</p></div></div>
			<div class="chip-groups">${CONTROL_GROUPS.map(([title, keys]) => [title, keys.filter((k) => allowed.includes(k))]).filter(([, keys]) => keys.length).map(
				([title, keys]) => `<div><div class="group-title">${title}</div><div class="chips">${keys
					.map(
						(k) => `<a class="chip ${k === kind ? "on" : ""}" href="#/control/${k}">${CONTROL_TITLES[k]}${
							counts[k] !== undefined ? ` <span class="n ${counts[k] && ALARM_CONTROLS.has(k) ? "alarm" : ""}">${fmtNum(counts[k])}</span>` : ""
						}</a>`
					)
					.join("")}</div></div>`
			).join("")}</div>
			${block(null, CONTROL_TITLES[kind], CONTROL_HELP[kind], `<div class="suppress-bar"></div><div class="card ctl"><div class="loading"><div class="spinner"></div></div></div>`)}`;
		const data = await api("control", { kind, show_suppressed: showSuppressed ? 1 : 0 });
		const box = view.querySelector(".ctl");
		const bar = view.querySelector(".suppress-bar");
		const reload = () => loadDashboard(true).then(() => viewControl(view, kind, showSuppressed));
		const canMark = kind === "events" && can.suppress;
		const canSuppress = data.suppressible && can.suppress && !showSuppressed;
		const canRestore = can.suppress && (showSuppressed || kind === "journal");
		const selected = new Set();

		const columns = showSuppressed
			? [...data.columns, { key: "suppression_reason", label: "Почему погашено" }, { key: "suppressed_until", label: "До" }]
			: data.columns;
		let actions = null;
		if (canMark) actions = (r) => `<button class="btn small mark" data-event="${esc(r.name)}">Обработано</button>`;
		if (canSuppress)
			actions = (r) => `<label class="pick" title="Отметить, чтобы погасить"><input type="checkbox" data-key="${esc(r.alert_key)}"></label>`;
		if (canRestore)
			actions = (r) =>
				kind === "journal" && r.status !== "Погашено" ? "" : `<button class="btn small restore" data-name="${esc(r.suppression || r.name)}">Вернуть</button>`;

		const drawBar = () => {
			if (!data.suppressible && kind !== "journal") return (bar.innerHTML = "");
			bar.innerHTML = `<div class="toolbar">
				${canSuppress ? `<button class="btn primary do-suppress" ${selected.size ? "" : "disabled"}>Погасить выбранные${selected.size ? ` · ${selected.size}` : ""}</button>
					<button class="btn pick-all">Отметить все</button>` : ""}
				${data.suppressible ? `<button class="btn toggle-suppressed">${showSuppressed ? "← Открытые замечания" : `Показать погашенные · ${fmtNum(data.suppressed)}`}</button>` : ""}
				${kind !== "journal" && allowed.includes("journal") ? `<a class="btn" href="#/control/journal">Журнал гашений</a>` : ""}
			</div>`;
			bar.querySelector(".do-suppress")?.addEventListener("click", () =>
				modal({
					title: `Погасить: ${selected.size} ${plural(selected.size, "замечание", "замечания", "замечаний")}`,
					text: "Замечание пропадёт из списка и счётчиков. В журнал попадут причина, кто и когда погасил. Вернуть можно в любой момент.",
					fields: [
						{ name: "reason", label: "Почему гасим (обязательно)", type: "textarea", required: true },
						{ name: "valid_to", label: "Погасить до (пусто — бессрочно; после даты замечание вернётся)", type: "date" },
					],
					submit: async (values) => {
						const n = await api("suppress_alerts", { kind, keys: [...selected], ...values }, true);
						toast(`Погашено: ${n}`);
						reload();
					},
				})
			);
			bar.querySelector(".pick-all")?.addEventListener("click", () => {
				box.querySelectorAll("input[data-key]").forEach((c) => ((c.checked = true), selected.add(c.dataset.key)));
				drawBar();
			});
			bar.querySelector(".toggle-suppressed")?.addEventListener("click", () => viewControl(view, kind, !showSuppressed));
		};

		table(box, {
			name: CONTROL_TITLES[kind].toLowerCase() + (showSuppressed ? "-погашенные" : ""),
			rows: data.rows,
			columns,
			empty: showSuppressed ? "Погашенных замечаний нет" : kind === "journal" ? "Журнал пуст" : "Замечаний нет",
			actions,
		});
		drawBar();
		box.addEventListener("change", (e) => {
			const c = e.target.closest("input[data-key]");
			if (!c) return;
			c.checked ? selected.add(c.dataset.key) : selected.delete(c.dataset.key);
			drawBar();
		});
		box.addEventListener("click", async (e) => {
			const mark = e.target.closest(".mark");
			if (mark) {
				mark.disabled = true;
				await api("mark_event_processed", { event: mark.dataset.event }, true);
				mark.closest("tr").style.opacity = ".4";
				mark.textContent = "✓";
				return;
			}
			const restore = e.target.closest(".restore");
			if (restore)
				modal({
					title: "Вернуть замечание",
					text: "Замечание снова появится в списке и счётчиках. В журнале останется, кто и почему его вернул.",
					fields: [{ name: "reason", label: "Почему возвращаем (обязательно)", type: "textarea", required: true }],
					submit: async (values) => {
						await api("restore_alert", { name: restore.dataset.name, ...values }, true);
						toast("Замечание возвращено");
						reload();
					},
				});
		});
	}

	// ------------------------------------------------------------------ access catalog

	async function viewAccess(view) {
		const rows = await api("entitlements");
		const systems = [...new Set(rows.map((r) => r.system))];
		let current = "";
		view.innerHTML = `
			<div class="page-head"><div><h1>Права доступа</h1><p>Каталог того, что выдаётся в системах: профили 1С, группы AD, группы и права Битрикс24.
				У каждого права — владелец и уровень риска.</p></div>
				${state.boot.can.roles ? `<div style="display:flex;gap:8px"><a class="btn" href="/app/query-report/Unmanaged Access">Доступы вне каталога</a><a class="btn primary" href="/app/entitlement/new">Добавить право</a></div>` : ""}</div>
			<div class="chips" style="margin-bottom:14px">${["", ...systems].map((s) => `<button class="chip ${s === current ? "on" : ""}" data-s="${esc(s)}">${esc(s || "Все")} <span class="n">${rows.filter((r) => !s || r.system === s).length}</span></button>`).join("")}</div>
			<div class="card ents"></div>`;
		const draw = () => {
			view.querySelectorAll("[data-s]").forEach((b) => b.classList.toggle("on", b.dataset.s === current));
			table(view.querySelector(".ents"), {
				name: "права-доступа",
				rows: rows.filter((r) => !current || r.system === current),
				empty: "Каталог пуст. Начните с отчёта «Подбор ролей» или «Доступы вне каталога».",
				columns: [
					{ key: "title", label: "Право доступа", render: (r) => `<a href="#/entitlement/${enc(r.name)}"><b>${esc(r.title)}</b></a>${r.active ? "" : " " + pill("не используется", "")}` },
					{ key: "system", label: "Система", type: "badge" },
					{ key: "risk", label: "Риск", render: (r) => `${pill(r.risk, RISK_TONE[r.risk])} ${r.privileged ? pill("привилегированное", "t-violet") : ""}` },
					{ key: "holders", label: "Есть у", type: "number" },
					{ key: "roles", label: "В ролях", type: "number" },
					{ key: "owner_name", label: "Владелец" },
				],
			});
		};
		view.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => ((current = b.dataset.s), draw())));
		draw();
	}

	async function viewEntitlement(view, name) {
		const d = await api("entitlement", { name });
		const e = d.doc;
		const link = e.ib_profile || e.ad_group || e.b24_workgroup || [e.b24_via, e.b24_resource].filter(Boolean).join(" — ") || e.other_reference;
		view.innerHTML = `
			<div class="crumbs"><a href="#/access">Права доступа</a> / ${esc(e.title)}</div>
			<div class="page-head"><div><h1>${esc(e.title)}</h1>
				<p>${systemBadge(e.system)} ${pill(e.risk, RISK_TONE[e.risk])} ${e.privileged ? pill("привилегированное", "t-violet") : ""} ${d.owner ? `· владелец: ${esc(d.owner)}` : ""}</p>
				${e.description ? `<p>${esc(e.description)}</p>` : ""}<p class="muted small">В системе: ${esc(link || "—")}</p></div>
				${DESK ? `<a class="btn" href="${deskUrl("Entitlement", e.name)}" target="_blank" rel="noopener">${icon("external")} Открыть</a>` : ""}</div>
			<div class="split">
				<div class="card"><div class="card-head"><h3>Есть у сотрудников · ${d.holders.length}</h3></div><div class="holders"></div></div>
				<div>
					<div class="card list"><div class="card-head" style="padding-bottom:10px"><h3>Положено по ролям</h3></div>
						${d.roles.map((r) => `<a class="list-item" href="#/role/${enc(r.role)}"><div class="grow"><b>${esc(r.role)}</b><small>${esc(r.requirement)}</small></div>→</a>`).join("") || `<div class="empty">Ни в одной роли</div>`}</div>
					<div class="card list" style="margin-top:16px"><div class="card-head" style="padding-bottom:10px"><h3>Нужно в процессах</h3></div>
						${d.process_roles.map((r) => `<div class="list-item"><div class="grow"><b>${esc(r.role_name)}</b><small>${esc(r.process_title)} · ${esc(r.requirement)}</small></div></div>`).join("") || `<div class="empty">Ни в одном процессе</div>`}</div>
				</div>
			</div>`;
		table(view.querySelector(".holders"), {
			name: "держатели-" + e.title,
			rows: d.holders,
			empty: "Ни у кого нет",
			columns: [
				{ key: "full_name", label: "Сотрудник", type: "person" },
				{ key: "status", label: "Статус", type: "status" },
				{ key: "evidence", label: "Где есть" },
				{ key: "exc", label: "", render: (r) => (d.exceptions.some((x) => x.person === r.person) ? pill("исключение", "t-violet") : "") },
			],
		});
	}

	// ------------------------------------------------------------------ roles

	async function viewRoles(view) {
		const rows = await api("roles");
		let num = 0;
		const group = (title, lead, list) =>
			list.length
				? block(++num, title, lead, `<div class="grid grid-3">${list
						.map(
							(r) => `<a class="card card-pad" href="#/role/${enc(r.name)}" style="color:inherit;text-decoration:none">
								<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start"><b style="font-size:15px">${esc(r.role_name)}</b>${pill(r.kind, r.kind === "Базовая" ? "t-green" : r.kind === "Дополнительная" ? "t-violet" : "t-blue")}</div>
								<p class="muted small" style="margin:6px 0 12px;min-height:18px">${esc((r.description || "").slice(0, 140))}</p>
								<div style="display:flex;gap:16px"><span><b>${fmtNum(r.members)}</b> <span class="muted">${plural(r.members, "сотрудник", "сотрудника", "сотрудников")}</span></span>
								<span><b>${r.entitlements}</b> <span class="muted">${plural(r.entitlements, "право", "права", "прав")}</span></span></div>
								${r.owner_name ? `<div class="muted small" style="margin-top:8px">владелец: ${esc(r.owner_name)}</div>` : ""}</a>`
						)
						.join("")}</div>`)
				: "";
		view.innerHTML = `
			<div class="page-head"><div><h1>Роли доступа</h1><p>Роль — набор прав. Должностные роли получают по правилам (должность, подразделение, организация),
				базовые — все работающие, дополнительные — только назначением на срок.</p></div>
				${state.boot.can.roles ? `<div style="display:flex;gap:8px"><a class="btn" href="/app/query-report/Role Mining">Подбор ролей</a><a class="btn primary" href="/app/access-role/new">Новая роль</a></div>` : ""}</div>
			${group("Действуют", "По ним считается, что положено сотрудникам.", rows.filter((r) => r.status === "Действует"))}
			${group("Черновики — на проверку", "Предложены подбором ролей или заведены вручную; на сверку не влияют, пока не утверждены.", rows.filter((r) => r.status === "Черновик"))}
			${group("Архив", "Больше не действуют, хранятся для истории.", rows.filter((r) => r.status === "Архив"))}
			${rows.length ? "" : `<div class="card empty"><b>Ролей пока нет</b>Подбор ролей предложит черновики по доступам, которые уже выданы.</div>`}`;
	}

	async function viewRole(view, name) {
		const d = await api("role", { name });
		const r = d.doc;
		const rules = r.rules
			.map(
				(x) => `<div class="list-item"><div class="grow">${[
					x.position_title && `должность «${esc(x.position_title)}»`,
					x.department && `подразделение «${esc(x.department)}»${x.include_subdepartments ? " с подчинёнными" : ""}`,
					x.organization && `организация «${esc(x.organization)}»`,
					x.main_only && "только основное место работы",
				].filter(Boolean).join(", ")}</div></div>`
			)
			.join("");
		view.innerHTML = `
			<div class="crumbs"><a href="#/roles">Роли доступа</a> / ${esc(r.name)}</div>
			<div class="page-head"><div><h1>${esc(r.name)}</h1><p>${pill(r.kind, "t-blue")} ${pill(r.status, r.status === "Действует" ? "t-green" : "")} ${d.owner ? `· владелец: ${esc(d.owner)}` : ""}</p>
				${r.description ? `<p>${esc(r.description)}</p>` : ""}</div>
				${DESK || state.boot.can.roles ? `<a class="btn" href="${deskUrl("Access Role", r.name)}" target="_blank" rel="noopener">${icon("external")} Изменить</a>` : ""}</div>
			<div class="split">
				<div class="card"><div class="card-head"><h3>Сотрудники с ролью · ${d.members.length}</h3></div><div class="members"></div></div>
				<div>
					<div class="card list"><div class="card-head" style="padding-bottom:10px"><h3>Кому положена</h3></div>
						${r.kind === "Базовая" ? `<div class="list-item">всем работающим</div>` : r.kind === "Дополнительная" ? `<div class="list-item">только назначением вручную</div>` : rules || `<div class="empty">Правил нет</div>`}</div>
					<div class="card list" style="margin-top:16px"><div class="card-head" style="padding-bottom:10px"><h3>Что даёт · ${r.entitlements.length}</h3></div>
						${r.entitlements.map((e) => `<a class="list-item" href="#/entitlement/${enc(e.entitlement)}"><div class="grow"><b>${esc(e.title)}</b><small>${esc(e.system)} · ${esc(e.requirement)}</small></div>→</a>`).join("") || `<div class="empty">Прав нет</div>`}</div>
				</div>
			</div>`;
		table(view.querySelector(".members"), {
			name: "роль-" + r.name,
			rows: d.members,
			empty: r.status === "Действует" ? "Никто не подходит под правила" : "Роль не действует",
			columns: [
				{ key: "full_name", label: "Сотрудник", type: "person" },
				{ key: "position", label: "Должность" },
				{ key: "department", label: "Подразделение" },
				{ key: "reason", label: "Почему" },
				{ key: "missing", label: "Не хватает", render: (x) => (x.missing ? pill(String(x.missing), "t-amber") : `<span class="muted">—</span>`), csv: (x) => x.missing },
			],
		});
	}

	// ------------------------------------------------------------------ processes

	async function viewProcesses(view) {
		const rows = await api("processes");
		const depth = {};
		rows.forEach((r) => (depth[r.name] = r.parent_business_process ? (depth[r.parent_business_process] || 0) + 1 : 0));
		view.innerHTML = `
			<div class="page-head"><div><h1>Бизнес-процессы</h1><p>Реестр процессов: владелец, описание, регламент и схема, роли в процессе (RACI) и участники.
				Права, нужные ролям процессов, попадают в «положено» участникам.</p></div>
				<div style="display:flex;gap:8px;flex-wrap:wrap">
				<a class="btn" href="/api/method/access_registry.business_processes.api.download_template">${icon("download")} Скачать в Excel</a>
				${state.boot.can.processes ? `<a class="btn" href="/app/process-import/new">Загрузить из Excel</a><a class="btn primary" href="/app/business-process/new">Новый процесс</a>` : ""}</div></div>
			<div class="card procs"></div>`;
		table(view.querySelector(".procs"), {
			name: "процессы",
			rows,
			filter: rows.length > 10,
			empty: "Процессы ещё не описаны",
			columns: [
				{ key: "title", label: "Процесс", render: (r) => `<span class="indent" style="width:${depth[r.name] * 22}px"></span><a href="#/process/${enc(r.name)}"><b>${esc(r.title)}</b></a> ${r.process_code ? `<span class="muted small">${esc(r.process_code)}</span>` : ""}` },
				{ key: "level", label: "Уровень" },
				{ key: "status", label: "Статус", render: (r) => pill(r.status, r.status === "Действует" ? "t-green" : "") },
				{ key: "owner_name", label: "Владелец" },
				{ key: "roles", label: "Ролей", type: "number" },
				{ key: "risks", label: "Риски", render: (r) => (r.risks ? pill(String(r.risks), "t-red") : `<span class="muted">—</span>`), csv: (r) => r.risks },
			],
		});
	}

	function sanitize(html) {
		const doc = new DOMParser().parseFromString(html || "", "text/html");
		doc.querySelectorAll("script,style,iframe,object,embed,link,meta").forEach((n) => n.remove());
		doc.querySelectorAll("*").forEach((n) =>
			[...n.attributes].forEach((a) => {
				if (/^on/i.test(a.name) || (/^(href|src)$/i.test(a.name) && /^\s*javascript:/i.test(a.value))) n.removeAttribute(a.name);
			})
		);
		return doc.body.innerHTML;
	}

	async function viewProcess(view, name) {
		const d = await api("process", { name });
		const p = d.doc;
		const roles = d.roles
			.map(
				(r) => `<div class="card acct">
					<div class="acct-head"><b>${esc(r.role_name)}</b>${pill(r.raci, "t-blue")}
						${r.problems && r.problems !== "в порядке" ? `<span style="margin-left:auto">${pill("есть риски", "t-red")}</span>` : `<span style="margin-left:auto">${pill("в порядке", "t-green")}</span>`}</div>
					${r.description ? `<p class="muted small" style="margin:0 0 8px">${esc(r.description)}</p>` : ""}
					${r.problems && r.problems !== "в порядке" ? `<div class="alert red" style="margin:6px 0">${esc(r.problems)}</div>` : ""}
					<div class="list">${r.participants.map((x) => `<a class="list-item" style="padding:8px 0" href="#/person/${enc(x.person)}"><span class="avatar ${x.person_status === "Работает" ? "" : "gray"}">${esc(initials(x.full_name))}</span>
						<div class="grow"><b>${esc(x.full_name)}</b><small>${esc(x.participation)}${x.person_status !== "Работает" ? " · " + esc(x.person_status) : ""}${x.presence === "Длительное отсутствие" ? " · длительное отсутствие" : ""}</small></div></a>`).join("") || `<div class="muted small">Участников нет</div>`}</div>
					${r.filled_by_access_role ? `<div class="muted small" style="margin-top:6px">все с ролью доступа «${esc(r.filled_by_access_role)}»</div>` : ""}
					${r.entitlements.length ? `<div class="tags">${r.entitlements.map((e) => `<a class="tag" href="#/entitlement/${enc(e.entitlement)}">${esc(e.title)}</a>`).join("")}</div>` : ""}
				</div>`
			)
			.join("");
		view.innerHTML = `
			<div class="crumbs"><a href="#/processes">Бизнес-процессы</a> / ${d.parent ? esc(d.parent) + " / " : ""}${esc(p.title)}</div>
			<div class="page-head"><div><h1>${esc(p.title)} ${p.process_code ? `<span class="muted" style="font-weight:500">${esc(p.process_code)}</span>` : ""}</h1>
				<p>${pill(p.status, p.status === "Действует" ? "t-green" : "")} ${pill(p.level, "")} ${d.owner ? `· владелец: ${esc(d.owner)}` : ""} ${p.version ? `· версия ${esc(p.version)}` : ""}
				${p.effective_from ? `· действует с ${esc(fmtDate(p.effective_from))}` : ""}</p></div>
				<div style="display:flex;gap:8px">${p.regulation_url ? `<a class="btn" href="${esc(p.regulation_url)}" target="_blank" rel="noopener">Регламент</a>` : ""}
				${p.diagram ? `<a class="btn" href="${esc(p.diagram)}" target="_blank" rel="noopener">Схема</a>` : ""}
				${state.boot.can.processes ? `<a class="btn" href="${deskUrl("Business Process", p.name)}" target="_blank" rel="noopener">${icon("external")} Изменить</a>` : ""}</div></div>
			<div class="split">
				<div class="group"><h3>Роли в процессе</h3>${roles ? `<div class="grid grid-2">${roles}</div>` : `<div class="card empty">Роли не описаны</div>`}</div>
				<div class="group"><h3>О процессе</h3><div class="card card-pad facts">
					${p.goal ? `<h4>Цель и результат</h4><p class="rich">${esc(p.goal)}</p>` : ""}
					${p.trigger_event ? `<h4>Начинается с</h4><p class="rich">${esc(p.trigger_event)}</p>` : ""}
					${p.systems ? `<h4>Системы</h4><p class="rich">${esc(p.systems)}</p>` : ""}
					${p.description ? `<h4>Описание</h4><div class="rich">${sanitize(p.description)}</div>` : ""}
					${d.children.length ? `<h4>Подпроцессы</h4>${d.children.map((c) => `<div><a href="#/process/${enc(c.name)}">${esc(c.title)}</a></div>`).join("")}` : ""}
					${!p.goal && !p.description && !p.systems && !d.children.length ? `<div class="muted">Описание не заполнено</div>` : ""}
				</div></div>
			</div>`;
	}

	// ------------------------------------------------------------------ sources

	async function viewSources(view) {
		const d = state.dashboard || (await loadDashboard());
		view.innerHTML = `
			<div class="page-head"><div><h1>Источники</h1><p>Откуда реестр берёт данные и когда загружал их в последний раз. Реестр только читает системы;
				в Битрикс24 может дописывать отчество и дату рождения из кадров, если это включено.</p></div>
				${DESK ? `<a class="btn" href="/app/sync-log" target="_blank" rel="noopener">${icon("external")} Журнал синхронизаций</a>` : ""}</div>
			<div class="card src"></div>`;
		table(view.querySelector(".src"), {
			name: "источники",
			rows: d.sources,
			filter: false,
			empty: "Источники не подключены",
			columns: [
				{ key: "state", label: "", render: (s) => `<span class="dot ${s.state}"></span>`, csv: (s) => s.state },
				{ key: "kind", label: "Источник", type: "badge" },
				{ key: "name", label: "Код", render: (s) => (DESK ? `<a href="${deskUrl(s.doctype, s.name)}" target="_blank" rel="noopener"><b>${esc(s.name)}</b></a>` : `<b>${esc(s.name)}</b>`) },
				{ key: "title", label: "Название" },
				{ key: "last", label: "Последняя успешная загрузка", type: "datetime" },
				{ key: "status", label: "Последний запуск" },
			],
		});
	}

	// ------------------------------------------------------------------ access reviews

	async function viewReviews(view) {
		const can = state.boot.can;
		const campaignsOk = canSee("reviews");
		const [items, campaigns] = await Promise.all([api("my_reviews"), campaignsOk ? api("reviews") : Promise.resolve([])]);
		const byReview = {};
		items.forEach((i) => ((byReview[i.access_review] = byReview[i.access_review] || { title: i.review_title, due: i.due_date, text: i.review_description, people: {} }),
			(byReview[i.access_review].people[i.person] = byReview[i.access_review].people[i.person] || []).push(i)));
		const done = items.filter((i) => i.decision).length;
		view.innerHTML = `
			<div class="page-head"><div><h1>Пересмотр доступа</h1><p>Проверяющие подтверждают, что доступы сотрудников нужны для работы, или отмечают их на отзыв.
				Реестр сам ничего не отзывает: список на отзыв получают администраторы систем.</p></div>
				${can.roles ? `<a class="btn primary" href="/app/access-review/new">Новый пересмотр</a>` : ""}</div>
			${block(1, "Мои задания", "Сотрудники, чьи доступы вам нужно подтвердить или отметить на отзыв.", items.length ? `<div class="card card-pad" style="margin-bottom:24px"><div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center">
				<div><b>Мои задания</b><div class="muted small">решено ${done} из ${items.length}</div></div>
				<div class="meter" style="flex:1;max-width:420px"><i class="my-progress" style="width:${(100 * done) / items.length}%;background:var(--green)"></i></div></div></div>
				<div class="my-tasks"></div>` : `<div class="card empty"><b>Заданий нет</b>Когда начнётся пересмотр доступа, здесь появятся сотрудники, чьи доступы нужно подтвердить.</div>`)}
			${campaignsOk ? block(2, "Кампании", "Все пересмотры: сроки, ход и сколько доступов отмечено на отзыв.", `<div class="card campaigns"></div>`) : ""}`;
		const box = view.querySelector(".my-tasks");
		if (box) {
			const refresh = () => {
				const n = items.filter((i) => i.decision).length;
				view.querySelector(".my-progress").style.width = `${(100 * n) / items.length}%`;
				view.querySelector(".card-pad .muted.small").textContent = `решено ${n} из ${items.length}`;
				setReviewBadge(items.filter((i) => !i.decision).length);
			};
			box.innerHTML = Object.entries(byReview)
				.map(
					([review, r]) => `<div class="group"><h3>${esc(r.title)}${r.due ? ` · срок ${esc(fmtDate(r.due))}` : ""}</h3>
						${r.text ? `<p class="muted" style="margin:-4px 0 12px">${esc(r.text)}</p>` : ""}
						<div class="grid grid-2">${Object.entries(r.people)
							.sort(([, a], [, b]) => (a.some((i) => !i.decision) ? 0 : 1) - (b.some((i) => !i.decision) ? 0 : 1))
							.map(([person, list]) => reviewCard(review, person, list))
							.join("")}</div></div>`
				)
				.join("");
			box.addEventListener("click", async (e) => {
				const btn = e.target.closest("button[data-decide]");
				if (!btn) return;
				btn.disabled = true;
				try {
					if (btn.dataset.item) {
						const item = items.find((i) => i.name === btn.dataset.item);
						let comment = null;
						if (btn.dataset.decide === "Отозвать") {
							comment = prompt("Почему отозвать? (необязательно)", item.comment || "") ?? null;
						}
						await api("decide", { item: item.name, decision: btn.dataset.decide, comment }, true);
						item.decision = btn.dataset.decide;
					} else {
						await api("decide_person", { review: btn.dataset.review, person: btn.dataset.person, decision: btn.dataset.decide }, true);
						items.filter((i) => i.person === btn.dataset.person && i.access_review === btn.dataset.review && !i.decision).forEach((i) => (i.decision = btn.dataset.decide));
					}
					const card = btn.closest("[data-card]");
					const list = items.filter((i) => i.person === card.dataset.person && i.access_review === card.dataset.review);
					card.outerHTML = reviewCard(card.dataset.review, card.dataset.person, list);
					refresh();
				} catch (err) {
					toast(err.message);
					btn.disabled = false;
				}
			});
		}
		if (campaignsOk)
			table(view.querySelector(".campaigns"), {
				name: "пересмотры",
				rows: campaigns,
				filter: false,
				empty: "Пересмотров ещё не было",
				columns: [
					{ key: "title", label: "Пересмотр", render: (r) => `<a href="#/review/${enc(r.name)}"><b>${esc(r.title)}</b></a>` },
					{ key: "status", label: "Статус", render: (r) => pill(r.status, r.status === "Идёт" ? "t-blue" : r.status === "Завершён" ? "t-green" : "") },
					{ key: "due_date", label: "Срок", type: "deadline" },
					{ key: "progress", label: "Решено", render: (r) => (r.items_total ? `${r.items_done} из ${r.items_total}` : "—"), csv: (r) => r.items_done },
					{ key: "items_revoke", label: "На отзыв", type: "number" },
					{ key: "items_unassigned", label: "Без проверяющего", type: "number" },
				],
			});
	}

	function reviewCard(review, person, list) {
		const first = list[0];
		const left = list.filter((i) => !i.decision).length;
		const decisionPill = (d) => (d === "Оставить" ? pill("оставить", "t-green") : d === "Отозвать" ? pill("отозвать", "t-red") : "");
		return `<div class="card acct" data-card data-review="${esc(review)}" data-person="${esc(person)}">
			<div class="acct-head"><span class="avatar ${first.person_status === "Работает" ? "" : "gray"}">${esc(initials(first.full_name))}</span>
				<div style="flex:1;min-width:0"><b>${esc(first.full_name)}</b> ${first.person_status !== "Работает" ? statusPill(first.person_status) : ""}
				<div class="muted small">${esc([first.position, first.department].filter(Boolean).join(" · "))}</div></div>
				${left ? `<button class="btn small" data-decide="Оставить" data-review="${esc(review)}" data-person="${esc(person)}">Оставить всё (${left})</button>` : pill("готово", "t-green")}</div>
			<div class="list">${list
				.map(
					(i) => `<div class="list-item" style="padding:10px 0;gap:10px;flex-wrap:wrap">
						<div class="grow"><b>${esc(i.access_title)}</b> ${systemBadge(i.system)} ${i.risk && i.risk !== "Средний" ? pill(i.risk, RISK_TONE[i.risk]) : ""}
							<small>${esc(i.evidence || "")}${i.comment ? " · " + esc(i.comment) : ""}</small></div>
						${decisionPill(i.decision)}
						<span class="nowrap"><button class="btn small" data-decide="Оставить" data-item="${esc(i.name)}" ${i.decision === "Оставить" ? "disabled" : ""}>Оставить</button>
						<button class="btn small" data-decide="Отозвать" data-item="${esc(i.name)}" ${i.decision === "Отозвать" ? "disabled" : ""} style="color:var(--red)">Отозвать</button></span>
					</div>`
				)
				.join("")}</div></div>`;
	}

	async function viewReview(view, name) {
		const d = await api("review", { name });
		const r = d.doc;
		const done = r.items_total ? Math.round((100 * r.items_done) / r.items_total) : 0;
		let filter = "";
		view.innerHTML = `
			<div class="crumbs"><a href="#/reviews">Пересмотр доступа</a> / ${esc(r.title)}</div>
			<div class="page-head"><div><h1>${esc(r.title)}</h1><p>${pill(r.status, r.status === "Идёт" ? "t-blue" : r.status === "Завершён" ? "t-green" : "")}
				· проверяет: ${esc(r.reviewer_mode.toLowerCase())}${r.due_date ? ` · срок ${esc(fmtDate(r.due_date))}` : ""}</p></div>
				${state.boot.can.roles ? `<a class="btn" href="${deskUrl("Access Review", r.name)}" target="_blank" rel="noopener">${icon("external")} Открыть</a>` : ""}</div>
			<div class="grid grid-4" style="margin-bottom:16px">
				${kpi({ label: "Решено", display: `${done}%`, hint: `${r.items_done} из ${r.items_total}`, tone: done === 100 ? "tone-green" : "tone-amber" })}
				${kpi({ label: "На отзыв", value: r.items_revoke, tone: "tone-red", hint: "передать администраторам систем" })}
				${kpi({ label: "Без проверяющего", value: r.items_unassigned, tone: "tone-amber", hint: "руководитель или владелец не найден" })}
			</div>
			<div class="chips" style="margin-bottom:12px">${["", "Отозвать", "Оставить", "Без решения"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-f="${f}">${f || "Все"}</button>`).join("")}</div>
			<div class="card items"></div>`;
		const draw = () => {
			view.querySelectorAll("[data-f]").forEach((b) => b.classList.toggle("on", b.dataset.f === filter));
			table(view.querySelector(".items"), {
				name: "пересмотр-" + r.title,
				rows: d.items.filter((i) => !filter || (filter === "Без решения" ? !i.decision : i.decision === filter)),
				empty: "Нет доступов",
				columns: [
					{ key: "full_name", label: "Сотрудник", type: "person" },
					{ key: "person_status", label: "Статус", type: "status" },
					{ key: "access_title", label: "Доступ", render: (i) => (i.entitlement ? `<a href="#/entitlement/${enc(i.entitlement)}">${esc(i.access_title)}</a>` : esc(i.access_title)) },
					{ key: "system", label: "Система", type: "badge" },
					{ key: "reviewer_name", label: "Проверяющий" },
					{ key: "decision", label: "Решение", render: (i) => (i.decision === "Отозвать" ? pill("отозвать", "t-red") : i.decision === "Оставить" ? pill("оставить", "t-green") : `<span class="muted">—</span>`), csv: (i) => i.decision || "" },
					{ key: "comment", label: "Комментарий" },
				],
			});
		};
		view.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => ((filter = b.dataset.f), draw())));
		draw();
	}

	// ------------------------------------------------------------------ modal

	function modal({ title, text, fields, submit }) {
		const back = document.createElement("div");
		back.className = "modal-back";
		back.innerHTML = `<form class="modal"><h3>${esc(title)}</h3><p class="muted" style="margin:0">${esc(text || "")}</p>
			${fields.map((f) => `<label>${esc(f.label)}</label>${f.type === "textarea" ? `<textarea name="${f.name}" ${f.required ? "required" : ""}></textarea>` : `<input type="${f.type || "text"}" name="${f.name}" ${f.required ? "required" : ""}>`}`).join("")}
			<div class="error-box" style="padding:8px 0 0;display:none"></div>
			<div class="actions"><button type="button" class="btn cancel">Отмена</button><button class="btn primary">Сохранить</button></div></form>`;
		document.body.appendChild(back);
		const close = () => back.remove();
		back.querySelector(".cancel").addEventListener("click", close);
		back.addEventListener("click", (e) => e.target === back && close());
		back.querySelector("textarea, input")?.focus();
		back.querySelector("form").addEventListener("submit", async (e) => {
			e.preventDefault();
			const values = Object.fromEntries(new FormData(e.target).entries());
			try {
				await submit(values);
				close();
			} catch (err) {
				const box = back.querySelector(".error-box");
				box.style.display = "block";
				box.textContent = err.message;
			}
		});
	}

	// ------------------------------------------------------------------ reports

	async function viewReports(view) {
		const groups = await api("reports_catalog");
		view.innerHTML = `
			<div class="page-head"><div><h1>Отчёты</h1><p>Отчёты реестра: доступы в 1С, Active Directory, Битрикс24, общие папки, ролевая модель.
				Фильтры, сортировка, выгрузка в Excel — без входа в рабочее пространство.</p></div></div>
			${groups.map((g, i) => block(i + 1, g.group, "", `<div class="grid grid-3">${g.reports
				.map((r) => `<a class="card card-pad report-card" href="#/report/${enc(r.name)}"><b>${esc(r.title)}</b>
					<p class="muted small" style="margin-top:6px">${esc(r.description)}</p></a>`)
				.join("")}</div>`)).join("") || `<div class="card empty"><b>Отчётов нет</b>Попросите администратора открыть вам раздел «Отчёты».</div>`}`;
	}

	async function viewReport(view, name) {
		const meta = await api("report_meta", { name });
		const values = {};
		meta.filters.forEach((f) => {
			if (f.default !== null && f.default !== undefined) values[f.fieldname] = f.default;
		});
		const field = (f) => {
			const id = `f-${f.fieldname}`;
			const label = `<span class="filter-label">${esc(f.label)}${f.reqd ? " *" : ""}</span>`;
			const v = values[f.fieldname] ?? "";
			if (f.fieldtype === "Check")
				return `<label class="filter check"><input type="checkbox" data-f="${f.fieldname}" ${v ? "checked" : ""}> ${esc(f.label)}</label>`;
			if (f.fieldtype === "Select") {
				const opts = Array.isArray(f.options) ? f.options : [];
				return `<label class="filter">${label}<select class="field" data-f="${f.fieldname}">${(opts.includes("") ? opts : ["", ...opts])
					.map((o) => `<option value="${esc(o)}" ${String(v) === String(o) ? "selected" : ""}>${esc(o || "все")}</option>`).join("")}</select></label>`;
			}
			if (f.fieldtype === "Link" && f.link_options)
				return `<label class="filter">${label}<select class="field" data-f="${f.fieldname}"><option value="">все</option>${f.link_options
					.map((o) => `<option value="${esc(o.value)}" ${v === o.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>`;
			if (f.fieldtype === "Link" && f.options === "Person")
				return `<label class="filter person-filter">${label}<input class="field" data-person="${f.fieldname}" placeholder="ФИО" autocomplete="off">
					<input type="hidden" data-f="${f.fieldname}"><div class="results"></div></label>`;
			const type = f.fieldtype === "Date" ? "date" : f.fieldtype === "Int" ? "number" : "text";
			return `<label class="filter">${label}<input class="field" type="${type}" data-f="${f.fieldname}" value="${esc(v)}"></label>`;
		};
		view.innerHTML = `
			<div class="crumbs"><a href="#/reports">Отчёты</a> / ${esc(meta.group)}</div>
			<div class="page-head"><div><h1>${esc(meta.title)}</h1><p>${esc(meta.description)}</p></div>
				<div style="display:flex;gap:8px"><button class="btn xlsx">${icon("download")} Excel</button></div></div>
			${meta.filters.length ? `<div class="card card-pad filters">${meta.filters.map(field).join("")}
				<button class="btn primary run">Показать</button></div>` : ""}
			<div class="report-message"></div>
			<div class="card rep" style="margin-top:16px"></div>`;
		const read = () => {
			const out = {};
			view.querySelectorAll("[data-f]").forEach((el) => {
				const v = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value;
				if (v !== "" && v !== 0) out[el.dataset.f] = v;
			});
			return out;
		};
		const box = view.querySelector(".rep");
		const run = async () => {
			const filters = read();
			const missing = meta.filters.filter((f) => f.reqd && !filters[f.fieldname]);
			if (missing.length) {
				box.innerHTML = `<div class="empty"><b>Заполните фильтр: ${esc(missing.map((f) => f.label).join(", "))}</b></div>`;
				return;
			}
			box.innerHTML = `<div class="loading"><div class="spinner"></div></div>`;
			try {
				const data = await api("run_report", { name, filters: JSON.stringify(filters) });
				view.querySelector(".report-message").innerHTML = data.message ? `<div class="alert amber">${esc(String(data.message).replace(/<[^>]+>/g, ""))}</div>` : "";
				table(box, { name: meta.title, rows: data.rows, columns: data.columns, empty: "Нет данных с такими фильтрами", pageSize: 200 });
			} catch (e) {
				box.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
			}
		};
		view.querySelector(".run")?.addEventListener("click", run);
		view.querySelectorAll("select[data-f], input[type=checkbox][data-f]").forEach((el) => el.addEventListener("change", run));
		view.querySelectorAll("input[data-f]:not([type=checkbox]):not([type=hidden])").forEach((el) => el.addEventListener("keydown", (e) => e.key === "Enter" && run()));
		view.querySelectorAll("[data-person]").forEach((input) => {
			const wrap = input.closest(".person-filter");
			const hidden = wrap.querySelector("[data-f]");
			const results = wrap.querySelector(".results");
			let timer;
			input.addEventListener("input", () => {
				hidden.value = "";
				clearTimeout(timer);
				timer = setTimeout(async () => {
					if (input.value.trim().length < 2) return results.classList.remove("open");
					const found = (await api("search", { query: input.value })).filter((r) => r.kind === "person");
					results.innerHTML = found.map((r) => `<a class="result" data-id="${esc(r.id)}" data-t="${esc(r.title)}"><div><b>${esc(r.title)}</b><small>${esc(r.subtitle || "")}</small></div></a>`).join("") || `<div class="empty">Не найдено</div>`;
					results.classList.add("open");
				}, 250);
			});
			results.addEventListener("click", (e) => {
				const r = e.target.closest("[data-id]");
				if (!r) return;
				hidden.value = r.dataset.id;
				input.value = r.dataset.t;
				results.classList.remove("open");
				run();
			});
		});
		view.querySelector(".xlsx").addEventListener("click", () => {
			const qs = new URLSearchParams({ name, filters: JSON.stringify(read()) });
			window.location.href = `${API}export_report?${qs}`;
		});
		if (!meta.filters.some((f) => f.reqd)) run();
		else box.innerHTML = `<div class="empty"><b>Выберите ${esc(meta.filters.filter((f) => f.reqd).map((f) => f.label).join(", "))}</b></div>`;
	}

	// ------------------------------------------------------------------ access to the app (administrators)

	const LEVEL_NAMES = ["нет", "просмотр", "работа"];

	async function viewAppAccess(view) {
		const d = await api("access_admin");
		const sectionPills = (sections, personal, lists) =>
			Object.entries(d.sections)
				.filter(([code]) => sections[code])
				.map(([code, title]) => pill(`${title}${sections[code] > 1 ? " · работа" : ""}`, sections[code] > 1 ? "t-green" : ""))
				.join(" ") +
			(personal ? " " + pill("персональные данные", "t-amber") : "") +
			(sections.control && lists && lists.length ? `<div class="muted small" style="margin-top:6px">Списки «Контроля»: ${esc(lists.map((k) => d.controls[k]).join(", "))}</div>` : "");
		const profiles = d.profiles
			.map(
				(p, i) => `<div class="card card-pad ${p.enabled ? "" : "acct off"}">
					<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
						<div><b style="font-size:16px">${esc(p.profile_name)}</b> ${p.enabled ? "" : pill("выключен", "")}
						${p.description ? `<div class="muted small" style="margin-top:4px">${esc(p.description)}</div>` : ""}</div>
						<div style="display:flex;gap:6px"><button class="btn small edit" data-i="${i}">Изменить</button><button class="btn small hist" data-name="${esc(p.name)}">История</button></div></div>
					<div class="tags" style="margin-top:12px">${sectionPills(p.sections, p.personal, p.control_lists) || `<span class="muted">разделы не выбраны</span>`}</div>
					<div class="group-title" style="margin:16px 0 8px">Пользователи · ${p.members.length}</div>
					<div class="tags">${p.members.map((m) => `<span class="tag">${esc(m.full_name)}</span>`).join("") || `<span class="muted small">никому не выдан</span>`}</div>
				</div>`
			)
			.join("");
		view.innerHTML = `
			<div class="page-head"><div><h1>Доступ к приложению</h1><p>Кто какие разделы видит. Профиль — набор разделов с уровнем («просмотр» или «работа»); выдайте его людям,
				и приложение откроется им без ролей desk. Доступы из нескольких профилей и ролей складываются. Все изменения профилей сохраняются в истории.</p></div>
				<button class="btn primary new">Новый профиль</button></div>
			${block(1, "Профили доступа", "Разделы, уровень и кому выдан профиль.", profiles ? `<div class="grid grid-2">${profiles}</div>` : `<div class="card empty"><b>Профилей пока нет</b>Создайте первый: например «Главный бухгалтер» — сотрудники, права доступа и роли на просмотр.</div>`)}
			${block(2, "Кто что видит", "Итоговый доступ каждого пользователя и откуда он: из ролей реестра или из профилей.", `<div class="card who"></div>`)}
			${block(3, "Роли реестра", "Роли desk по-прежнему дают доступ к приложению — как встроенные профили. Назначаются в карточке пользователя Frappe.",
				`<div class="card list">${d.roles.map((r) => `<div class="list-item"><div class="grow"><b>${esc(r.role)}</b><small>${esc(r.gives)}</small></div></div>`).join("")}</div>`)}`;
		const short = (level) => (level > 1 ? "работа" : level ? "✓" : "");
		table(view.querySelector(".who"), {
			name: "доступ-к-приложению",
			rows: d.users.map((u) => ({ ...u, ...Object.fromEntries(Object.keys(d.sections).map((k) => ["s_" + k, short(u.sections[k])])),
				pd: u.personal ? "✓" : "", via_text: u.via.join(", "), lists_text: u.lists ? u.lists.map((k) => d.controls[k]).join(", ") : "" })),
			empty: "Доступа пока ни у кого нет",
			columns: [
				{ key: "full_name", label: "Пользователь" },
				...Object.entries(d.sections).map(([k, title]) => ({ key: "s_" + k, label: title.replace("Пересмотр доступа: кампании", "Пересмотр") })),
				{ key: "pd", label: "Перс. данные" },
				{ key: "lists_text", label: "Только списки" },
				{ key: "via_text", label: "Через" },
			],
		});
		view.querySelector(".new").addEventListener("click", () => editProfile(d, null, () => viewAppAccess(view)));
		view.querySelectorAll(".edit").forEach((b) => b.addEventListener("click", () => editProfile(d, d.profiles[+b.dataset.i], () => viewAppAccess(view))));
		view.querySelectorAll(".hist").forEach((b) =>
			b.addEventListener("click", async () => {
				const rows = await api("profile_history", { name: b.dataset.name });
				const back = document.createElement("div");
				back.className = "modal-back";
				back.innerHTML = `<div class="modal wide"><h3>История: ${esc(b.dataset.name)}</h3>
					<div class="list" style="max-height:60vh;overflow:auto">${rows.map((r) => `<div class="list-item"><div class="grow"><b>${esc(r.who)}</b> <span class="muted small">${esc(fmtDateTime(r.when))}</span><small>${esc(r.what)}</small></div></div>`).join("") || `<div class="empty">Изменений нет</div>`}</div>
					<div class="actions"><button class="btn close">Закрыть</button></div></div>`;
				document.body.appendChild(back);
				back.querySelector(".close").addEventListener("click", () => back.remove());
				back.addEventListener("click", (e) => e.target === back && back.remove());
			})
		);
	}

	function editProfile(d, profile, done) {
		const p = profile || { profile_name: "", description: "", enabled: 1, sections: {}, personal: false, control_lists: [], reports: [], members: [] };
		const members = new Map(p.members.map((m) => [m.user, m.full_name]));
		const back = document.createElement("div");
		back.className = "modal-back";
		const sectionRow = ([code, title]) => {
			const levels = d.work_sections.includes(code) ? [0, 1, 2] : [0, 1];
			return `<div class="sec-row"><span>${esc(title)}</span><select class="field" data-sec="${code}">${levels
				.map((l) => `<option value="${l}" ${(p.sections[code] || 0) === l ? "selected" : ""}>${LEVEL_NAMES[l]}</option>`)
				.join("")}</select></div>`;
		};
		back.innerHTML = `<form class="modal wide"><h3>${profile ? "Профиль доступа" : "Новый профиль доступа"}</h3>
			<label>Название</label><input name="profile_name" value="${esc(p.profile_name)}" required>
			<label>Для кого и зачем</label><input name="description" value="${esc(p.description || "")}">
			<label>Разделы</label><div class="sec-grid">${Object.entries(d.sections).map(sectionRow).join("")}</div>
			<label class="check"><input type="checkbox" name="personal" ${p.personal ? "checked" : ""}> Персональные данные (даты рождения)</label>
			<div class="lists-box"><label>Списки «Контроля» <span class="muted">(ничего не отмечено — все)</span></label><div class="sec-grid">${Object.entries(d.controls)
				.map(([k, t]) => `<label class="check"><input type="checkbox" data-list="${k}" ${p.control_lists.includes(k) ? "checked" : ""}> ${esc(t)}</label>`)
				.join("")}</div></div>
			<div class="reports-box"><label>Отчёты <span class="muted">(ничего не отмечено — все)</span></label>${d.report_catalog
				.map((g) => `<div class="group-title" style="margin:12px 0 6px">${esc(g.group)}</div><div class="sec-grid">${g.reports
					.map((r) => `<label class="check"><input type="checkbox" data-report="${esc(r.name)}" ${(p.reports || []).includes(r.name) ? "checked" : ""}> ${esc(r.title)}${r.personal ? " · перс. данные" : ""}</label>`)
					.join("")}</div>`)
				.join("")}</div>
			<label>Пользователи</label><div class="tags members"></div>
			<div style="position:relative"><input class="user-q" placeholder="Найти пользователя по имени или почте" autocomplete="off"><div class="results user-results"></div></div>
			<label class="check"><input type="checkbox" name="enabled" ${p.enabled ? "checked" : ""}> Профиль действует</label>
			<div class="error-box" style="padding:8px 0 0;display:none"></div>
			<div class="actions"><button type="button" class="btn cancel">Отмена</button><button class="btn primary">Сохранить</button></div></form>`;
		document.body.appendChild(back);
		const form = back.querySelector("form");
		const drawMembers = () => {
			form.querySelector(".members").innerHTML =
				[...members].map(([u, n]) => `<span class="tag">${esc(n)} <a href="#" data-rm="${esc(u)}" title="Убрать">×</a></span>`).join("") ||
				`<span class="muted small">никому не выдан</span>`;
		};
		const toggleLists = () => {
			form.querySelector(".lists-box").hidden = form.querySelector('[data-sec="control"]').value === "0";
			form.querySelector(".reports-box").hidden = form.querySelector('[data-sec="reports"]').value === "0";
		};
		drawMembers();
		toggleLists();
		form.querySelector('[data-sec="control"]').addEventListener("change", toggleLists);
		form.querySelector('[data-sec="reports"]').addEventListener("change", toggleLists);
		form.querySelector(".members").addEventListener("click", (e) => {
			const rm = e.target.closest("[data-rm]");
			if (!rm) return;
			e.preventDefault();
			members.delete(rm.dataset.rm);
			drawMembers();
		});
		const q = form.querySelector(".user-q");
		const results = form.querySelector(".user-results");
		let timer;
		q.addEventListener("input", () => {
			clearTimeout(timer);
			timer = setTimeout(async () => {
				if (q.value.trim().length < 2) return results.classList.remove("open");
				const users = await api("find_users", { query: q.value });
				results.innerHTML = users.map((u) => `<a class="result" data-u="${esc(u.user)}" data-n="${esc(u.full_name || u.user)}"><div><b>${esc(u.full_name || u.user)}</b><small>${esc(u.user)}</small></div></a>`).join("") || `<div class="empty">Не найдено</div>`;
				results.classList.add("open");
			}, 250);
		});
		results.addEventListener("click", (e) => {
			const r = e.target.closest("[data-u]");
			if (!r) return;
			members.set(r.dataset.u, r.dataset.n);
			q.value = "";
			results.classList.remove("open");
			drawMembers();
		});
		back.querySelector(".cancel").addEventListener("click", () => back.remove());
		form.addEventListener("submit", async (e) => {
			e.preventDefault();
			const sections = Object.fromEntries([...form.querySelectorAll("[data-sec]")].map((x) => [x.dataset.sec, +x.value]));
			const data = {
				name: profile ? profile.name : null,
				profile_name: form.profile_name.value,
				description: form.description.value,
				enabled: form.enabled.checked ? 1 : 0,
				personal: form.personal.checked ? 1 : 0,
				sections,
				control_lists: [...form.querySelectorAll("[data-list]:checked")].map((x) => x.dataset.list),
				reports: [...form.querySelectorAll("[data-report]:checked")].map((x) => x.dataset.report),
				members: [...members.keys()],
			};
			try {
				await api("save_profile", { data }, true);
				back.remove();
				toast("Профиль сохранён");
				done();
			} catch (err) {
				const box = form.querySelector(".error-box");
				box.style.display = "block";
				box.textContent = err.message;
			}
		});
	}

	// ------------------------------------------------------------------ start

	async function start() {
		const theme = storage("registry-theme");
		if (theme) document.documentElement.dataset.theme = theme;
		try {
			state.boot = await api("bootstrap");
		} catch (e) {
			$app.innerHTML = `<main class="denied"><h1>Реестр недоступен</h1><p>${esc(e.message)}</p></main>`;
			return;
		}
		renderShell();
		window.addEventListener("hashchange", route);
		await route();
		if (!state.dashboard && (canSee("overview") || canSee("control") || canSee("sources"))) loadDashboard().catch(() => null);
	}

	start();
})();
