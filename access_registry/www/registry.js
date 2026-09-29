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
		home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v10h14V10"/>',
		people: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14.7c1.9.7 3.1 2.4 3.5 5.3"/>',
		key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9"/><path d="m17 6 3 3"/><path d="m14 9 2 2"/>',
		roles: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 9h8M8 13h8M8 17h5"/>',
		flow: '<rect x="3" y="3" width="7" height="6" rx="1.5"/><rect x="14" y="15" width="7" height="6" rx="1.5"/><path d="M6.5 9v4a2 2 0 0 0 2 2H14"/>',
		shield: '<path d="M12 3 4.5 6v6c0 4.5 3.2 8 7.5 9 4.3-1 7.5-4.5 7.5-9V6z"/><path d="m9 12 2 2 4-4"/>',
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
				return fmtNum(v);
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
		["people", "#/people", "Сотрудники"],
		["shield", "#/control", "Контроль", "control"],
		["key", "#/access", "Права доступа"],
		["roles", "#/roles", "Роли доступа"],
		["flow", "#/processes", "Бизнес-процессы"],
		["check", "#/reviews", "Пересмотр доступа", "reviews"],
		["db", "#/sources", "Источники"],
	];

	function renderShell() {
		const b = state.boot;
		$app.innerHTML = `
			<div class="shell">
				<aside class="sidebar">
					<div class="brand"><div class="brand-mark">${icon("shield").replace("<svg", '<svg style="width:18px;height:18px;stroke:#fff;fill:none;stroke-width:2"')}</div>
						<div>Реестр доступа<small>кто есть кто и у кого что</small></div></div>
					<nav class="nav">
						${NAV.filter(([, href]) => b.can.read || href === "#/reviews").map(
							([ic, href, label, badge]) =>
								`<a href="${href}" data-nav="${href}">${icon(ic)}<span>${label}</span>${badge ? `<span class="count" data-badge="${badge}" hidden></span>` : ""}</a>`
						).join("")}
					</nav>
					<div class="sidebar-foot">
						<div class="user"><span class="avatar">${esc(initials(b.user.full_name))}</span><div><b>${esc(b.user.full_name)}</b><span class="muted small">${esc(
							b.can.admin ? "администратор" : b.can.audit ? "аудитор" : b.can.roles ? "ролевая модель" : b.can.processes ? "процессы" : "просмотр"
						)}</span></div></div>
						<div class="links">${DESK || b.can.roles || b.can.processes ? `<a href="/app/access-registry">Рабочее пространство</a>` : ""}<a href="/?cmd=web_logout">Выйти</a></div>
					</div>
				</aside>
				<div class="main">
					<header class="topbar">
						<button class="icon-btn menu-toggle" aria-label="Меню">${icon("menu")}</button>
						<div class="search" ${b.can.read ? "" : "hidden"}>
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
	];

	async function route() {
		let path = decodeURIComponent((location.hash || "#/").slice(1)) || "/";
		if (!state.boot.can.read && !path.startsWith("/review")) path = "/reviews";
		const view = document.getElementById("view");
		$app.querySelector(".shell").classList.remove("nav-open");
		$app.querySelectorAll("[data-nav]").forEach((a) => {
			const href = a.dataset.nav.slice(1);
			a.classList.toggle("active", href === "/" ? path === "/" : path.startsWith(href) || (href === "/people" && path.startsWith("/person")) ||
				(href === "/access" && path.startsWith("/entitlement")) || (href === "/roles" && path.startsWith("/role/")) || (href === "/processes" && path.startsWith("/process/")) || (href === "/reviews" && path.startsWith("/review/")));
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
		const alarms = d.dismissed_access.people + d.sod;
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
			actions: can.roles
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
		kind = kind || "dismissed";
		const d = state.dashboard || (await loadDashboard());
		const can = state.boot.can;
		const counts = {
			dismissed: d.dismissed_access.people,
			unlinked: Object.values(d.unlinked).reduce((a, x) => a + x, 0),
			excess: d.reconciliation.excess + d.reconciliation.excess_not_working,
			missing: d.reconciliation.missing,
			sod: d.sod,
			exceptions: d.reconciliation.exceptions,
			processes: d.processes.risks,
			events: d.events,
			journal: d.suppressed,
		};
		view.innerHTML = `
			<div class="page-head"><div><h1>Контроль</h1><p>Что требует решения: списки для службы безопасности, ИБ и контролёров прав. Каждый список можно выгрузить в CSV.
				Замечание, которое разобрали и приняли (например, учётка подрядчика без сотрудника), можно погасить с комментарием — оно попадёт в журнал.</p></div></div>
			<div class="chip-groups">${CONTROL_GROUPS.map(
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
		const canMark = kind === "events" && (can.audit || can.roles);
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
				${kind !== "journal" ? `<a class="btn" href="#/control/journal">Журнал гашений</a>` : ""}
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
		const [items, campaigns] = await Promise.all([api("my_reviews"), can.read ? api("reviews") : Promise.resolve([])]);
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
			${can.read ? block(2, "Кампании", "Все пересмотры: сроки, ход и сколько доступов отмечено на отзыв.", `<div class="card campaigns"></div>`) : ""}`;
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
		if (can.read)
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
		if (!state.dashboard && state.boot.can.read) loadDashboard().catch(() => null);
	}

	start();
})();
