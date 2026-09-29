// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

const AO_API = "access_registry.access_registry.page.access_overview.access_overview.";

frappe.pages["access-overview"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Обзор доступа"),
		single_column: true,
	});
	wrapper.access_overview = new AccessOverview(page);
};

frappe.pages["access-overview"].on_page_show = function (wrapper) {
	const view = wrapper.access_overview;
	if (!view) return;
	const options = frappe.route_options || {};
	frappe.route_options = null;
	if (options.person) view.open_person(options.person);
	else if (options.account) view.open_account(options.account);
	else if (options.ad_account) view.open_ad_account(options.ad_account);
};

class AccessOverview {
	constructor(page) {
		this.page = page;
		this.tab = "overview";
		this.matrix_filters = { organization: "", only_working: 1 };
		this.$root = $(`<div class="ao-root"></div>`).appendTo(page.main);
		this.page.set_secondary_action(__("Обновить"), () => this.refresh(), "refresh");
		this.render_shell();
		this.show_tab("overview");
	}

	// ------------------------------------------------------------------ helpers

	api(method, args) {
		return frappe.call({ method: AO_API + method, args: args || {} }).then((r) => r.message);
	}

	esc(value) {
		return frappe.utils.escape_html(value == null ? "" : String(value));
	}

	when(value) {
		if (!value) return __("никогда");
		const d = frappe.datetime.str_to_obj(value);
		const pad = (n) => String(n).padStart(2, "0");
		return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}

	date(value) {
		if (!value) return "";
		const [y, m, d] = String(value).split("-");
		return `${d}.${m}.${y}`;
	}

	initials(name) {
		return (name || "?")
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0])
			.join("")
			.toUpperCase();
	}

	status_pill(status) {
		if (!status) return "";
		const tone = { Работает: "green", Уволен: "gray", "Не принят": "gray", "Нет в выгрузке": "gray" }[status] || "gray";
		return `<span class="ao-pill ao-${tone}">${this.esc(status)}</span>`;
	}

	ad_badge(account) {
		if (account.missing_in_source) return `<span class="ao-pill ao-orange">${__("нет в AD")}</span>`;
		return account.enabled
			? `<span class="ao-login-state on">${__("включена")}</span>`
			: `<span class="ao-login-state off">${__("отключена")}</span>`;
	}

	config_badge(configuration) {
		const tone = configuration === "ЗУП" ? "green" : configuration === "Бухгалтерия" ? "green" : "gray";
		return `<span class="ao-badge ao-${tone}">${this.esc(configuration || "—")}</span>`;
	}

	sync_state(status, last) {
		if (!status && !last) return { tone: "gray", text: __("не загружалось") };
		const s = status || "";
		if (s.startsWith("Успех")) return { tone: "green", text: this.when(last) };
		if (s.startsWith("Остановлен")) return { tone: "gray", text: __("остановлено предохранителем") };
		if (s.startsWith("Ошибка")) return { tone: "gray", text: __("ошибка") };
		return { tone: "green", text: s };
	}

	loading() {
		return `<div class="ao-loading"><div class="ao-spinner"></div></div>`;
	}

	empty(text) {
		return `<div class="ao-empty">${this.esc(text)}</div>`;
	}

	// ------------------------------------------------------------------ shell

	render_shell() {
		this.$root.html(`
			<div class="ao-hero">
				<div class="ao-hero-text">
					<div class="ao-eyebrow">${__("Реестр доступа")}</div>
					<h2>${__("Кто есть кто и у кого какой доступ")}</h2>
					<p>${__("Кадры из ЗУП, учётки Active Directory, пользователи и права всех баз 1С — в одном месте.")}</p>
				</div>
				<div class="ao-search">
					<svg class="ao-search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
					<input type="search" placeholder="${__("Сотрудник, пользователь 1С, логин 1С или учётка AD")}" />
					<div class="ao-results"></div>
				</div>
			</div>
			<div class="ao-tabs">
				<button data-tab="overview">${__("Обзор")}</button>
				<button data-tab="person">${__("Сотрудник")}</button>
				<button data-tab="matrix">${__("Матрица доступа")}</button>
			</div>
			<div class="ao-view"></div>
		`);
		this.$view = this.$root.find(".ao-view");
		this.$root.on("click", ".ao-tabs button", (e) => this.show_tab($(e.currentTarget).data("tab")));
		this.bind_search();
		this.bind_navigation();
	}

	show_tab(tab) {
		this.tab = tab;
		this.$root.find(".ao-tabs button").removeClass("active").filter(`[data-tab="${tab}"]`).addClass("active");
		if (tab === "overview") this.render_overview();
		if (tab === "matrix") this.render_matrix();
		if (tab === "person") {
			if (this.current) this.render_card(this.current);
			else
				this.$view.html(
					this.empty(__("Найдите сотрудника, пользователя 1С или учётку AD через поиск вверху страницы."))
				);
		}
	}

	refresh() {
		if (this.tab === "person" && this.current_request) this.current_request();
		else this.show_tab(this.tab);
	}

	bind_navigation() {
		this.$root.on("click", "[data-person]", (e) => {
			e.preventDefault();
			this.open_person($(e.currentTarget).data("person"));
		});
		this.$root.on("click", "[data-account]", (e) => {
			e.preventDefault();
			this.open_account($(e.currentTarget).data("account"));
		});
		this.$root.on("click", "[data-ad-account]", (e) => {
			e.preventDefault();
			this.open_ad_account($(e.currentTarget).data("ad-account"));
		});
		this.$root.on("click", "[data-route]", (e) => {
			e.preventDefault();
			const $el = $(e.currentTarget);
			const route = String($el.data("route")).split("|");
			const options = $el.data("options");
			if (options) frappe.route_options = options;
			frappe.set_route(...route);
		});
		this.$root.on("click", ".ao-profile-head", (e) => {
			$(e.currentTarget).closest(".ao-profile").toggleClass("open");
		});
	}

	bind_search() {
		const $input = this.$root.find(".ao-search input");
		const $results = this.$root.find(".ao-results");
		const run = frappe.utils.debounce(() => {
			const query = $input.val();
			if ((query || "").trim().length < 2) {
				$results.removeClass("show").empty();
				return;
			}
			this.api("search", { query }).then((items) => {
				if (!items || !items.length) {
					$results.html(`<div class="ao-result-empty">${__("Ничего не найдено")}</div>`).addClass("show");
					return;
				}
				$results
					.html(
						items
							.map(
								(item) => `
							<a class="ao-result" ${
								{ person: "data-person", account: "data-account", ad: "data-ad-account" }[item.kind]
							}="${this.esc(item.id)}">
								<span class="ao-avatar ao-avatar-sm ${
									{ account: "ao-avatar-account", ad: "ao-avatar-ad" }[item.kind] || ""
								}">${this.esc({ account: "1С", ad: "AD" }[item.kind] || this.initials(item.title))}</span>
								<span class="ao-result-text">
									<span class="ao-result-title">${this.esc(item.title)} ${this.status_pill(item.status)}</span>
									<span class="ao-result-sub">${this.esc(item.subtitle)}</span>
								</span>
							</a>`
							)
							.join("")
					)
					.addClass("show");
			});
		}, 250);
		$input.on("input", run);
		$input.on("focus", () => $results.children().length && $results.addClass("show"));
		$(document).on("click", (e) => {
			if (!$(e.target).closest(".ao-search").length) $results.removeClass("show");
		});
		this.$root.on("click", ".ao-result", () => {
			$results.removeClass("show");
			$input.val("");
		});
	}

	// ------------------------------------------------------------------ overview

	render_overview() {
		this.$view.html(this.loading());
		this.api("get_overview").then((data) => {
			if (this.tab !== "overview") return;
			const k = data.kpis;
			const tile = (value, label, hint, tone, route, options) => `
				<a class="ao-kpi ao-${tone}" data-route="${route}" ${
				options ? `data-options='${this.esc(JSON.stringify(options))}'` : ""
			}>
					<div class="ao-kpi-value">${frappe.format(value, { fieldtype: "Int" })}</div>
					<div class="ao-kpi-label">${label}</div>
					<div class="ao-kpi-hint">${hint}</div>
				</a>`;
			const kpis = [
				tile(k.employees, __("Сотрудников работают"), __("по кадрам всех баз ЗУП"), "green", "List|Person", { status: "Работает" }),
				tile(k.users, __("Пользователей 1С со входом"), __("во всех базах"), "green", "List|IB User", {
					login_allowed: 1,
					invalid: 0,
					missing_in_source: 0,
				}),
				tile(k.not_working, __("Вход у неработающих"), __("уволены, но вход разрешён"), k.not_working ? "gray" : "gray", "query-report|IB Login Not Working"),
				tile(k.unlinked, __("Не привязаны к сотруднику"), __("со входом, без сотрудника"), k.unlinked ? "gray" : "gray", "query-report|IB Users Without Employee"),
				tile(k.extra_roles, __("Роли в обход профилей"), __("выданы напрямую"), k.extra_roles ? "gray" : "gray", "query-report|IB Extra Roles"),
				tile(k.orphans, __("Сироты ИБ"), __("пользователи ИБ без карточки"), k.orphans ? "gray" : "gray", "query-report|IB Orphans"),
			].join("");
			const ad_kpis = k.ad_domains
				? [
						tile(k.ad_enabled, __("Учёток AD включено"), __("во всех доменах"), "green", "List|AD Account", {
							enabled: 1,
							missing_in_source: 0,
						}),
						tile(k.ad_not_working, __("AD включена у неработающих"), __("уволены, учётка активна"), k.ad_not_working ? "gray" : "gray", "query-report|AD Dismissed Enabled"),
						tile(k.ad_off_1c_on, __("AD отключена, вход в 1С есть"), __("по «Пользователю ОС» в 1С"), k.ad_off_1c_on ? "gray" : "gray", "query-report|AD Disabled But 1C Active"),
						tile(k.ad_unlinked, __("Учётки AD без сотрудника"), __("включённые: служебные и не найденные"), k.ad_unlinked ? "gray" : "gray", "query-report|AD Without Employee"),
				  ].join("")
				: "";

			const review = [
				[k.merge_candidates, __("кандидатов на склейку"), "List|Person Merge Candidate", { status: "Открыт" }],
				[k.new_events, __("новых кадровых событий"), "List|HR Event", { processed: 0 }],
				[k.long_absence, __("в длительном отсутствии"), "List|Person", { presence: "Длительное отсутствие" }],
				[k.failed_syncs, __("неудачных загрузок"), "List|Sync Log", { status: ["in", ["Ошибка", "Остановлен предохранителем"]] }],
			]
				.map(
					([value, label, route, options]) => `
				<a class="ao-chip ${value ? "" : "ao-chip-muted"}" data-route="${route}" data-options='${this.esc(
						JSON.stringify(options)
					)}'><b>${value}</b> ${label}</a>`
				)
				.join("");

			this.$view.html(`
				<div class="ao-kpis">${kpis}</div>
				${ad_kpis ? `<div class="ao-kpi-group">Active Directory</div><div class="ao-kpis">${ad_kpis}</div>` : ""}
				<div class="ao-chips">${review}</div>
				<div class="ao-grid">
					<section class="ao-card ao-span-2">
						<header><h3>${__("Требует внимания")}</h3><span class="ao-muted">${__("первые записи, полный список — в отчётах")}</span></header>
						<div class="ao-attention ${k.ad_domains ? "ao-attention-4" : ""}">
							${this.attention_column(__("Вход у неработающих"), "gray", data.attention.not_working, (r) => ({
								title: r.full_name,
								sub: `${r.base_code} · ${r.login || r.user_name} · ${r.status}`,
								attrs: `data-person="${this.esc(r.person)}"`,
							}))}
							${this.attention_column(__("Не привязаны к сотруднику"), "gray", data.attention.unlinked, (r) => ({
								title: r.user_name,
								sub: `${r.base_code} · ${r.person_link_note || ""}`,
								attrs: `data-account="${this.esc(r.name)}"`,
							}))}
							${this.attention_column(__("Роли в обход профилей"), "gray", data.attention.extra_roles, (r) => ({
								title: r.user_name,
								sub: `${r.base_code} · ${r.roles.join(", ")}${r.more ? " +" + r.more : ""}`,
								attrs: r.person ? `data-person="${this.esc(r.person)}"` : `data-account="${this.esc(r.name)}"`,
							}))}
							${
								k.ad_domains
									? this.attention_column(__("Учётка AD включена у неработающих"), "gray", data.attention.ad_not_working, (r) => ({
											title: r.full_name,
											sub: `${r.domain} · ${r.sam_account_name || ""} · ${r.status}`,
											attrs: `data-person="${this.esc(r.person)}"`,
									  }))
									: ""
							}
						</div>
					</section>
					<section class="ao-card">
						<header><h3>${__("Базы 1С")}</h3><a class="ao-link" data-route="List|Info Base">${__("все базы")} →</a></header>
						<div class="ao-bases">${data.bases.map((b) => this.base_card(b)).join("") || this.empty(__("Баз пока нет"))}</div>
						<header class="ao-subheader"><h3>${__("Домены AD")}</h3><a class="ao-link" data-route="List|AD Domain">${__("все домены")} →</a></header>
						<div class="ao-bases">${
							(data.domains || []).map((d) => this.domain_card(d)).join("") ||
							`<a class="ao-base ao-disabled" data-route="Form|AD Domain|new">${__("Домен не подключён — добавить")}</a>`
						}</div>
					</section>
				</div>
			`);
		});
	}

	attention_column(title, tone, rows, map) {
		const items = (rows || [])
			.map((row) => {
				const item = map(row);
				return `<a class="ao-att-item" ${item.attrs}><span class="ao-dot ao-${tone}"></span><span><b>${this.esc(
					item.title
				)}</b><small>${this.esc(item.sub)}</small></span></a>`;
			})
			.join("");
		return `<div class="ao-att-col"><h4>${title}</h4>${items || `<div class="ao-ok">✓ ${__("всё в порядке")}</div>`}</div>`;
	}

	base_card(base) {
		const hr = this.sync_state(base.last_status, base.last_sync);
		const rights = this.sync_state(base.itaccess_last_status, base.itaccess_last_snapshot);
		const line = (label, state, show) =>
			show
				? `<div class="ao-sync"><span class="ao-dot ao-${state.tone}"></span><span>${label}</span><span class="ao-muted">${this.esc(
						state.text
				  )}</span></div>`
				: "";
		return `
			<a class="ao-base ${base.enabled ? "" : "ao-disabled"}" data-route="Form|Info Base|${this.esc(base.name)}">
				<div class="ao-base-head">
					<b>${this.esc(base.name)}</b>${this.config_badge(base.configuration)}
					${base.enabled ? "" : `<span class="ao-badge ao-gray">${__("выключена")}</span>`}
				</div>
				<div class="ao-base-title">${this.esc(base.title || "")}</div>
				<div class="ao-base-stats">
					${base.configuration === "ЗУП" ? `<span><b>${base.employments}</b> ${__("трудоустройств")}</span>` : ""}
					<span><b>${base.users}</b> ${__("пользователей со входом")}</span>
				</div>
				${line(__("Кадры"), hr, base.configuration === "ЗУП")}
				${line(__("Пользователи и права"), rights, base.itaccess_enabled)}
				${base.itaccess_enabled ? "" : `<div class="ao-sync ao-muted">${__("загрузка прав не настроена")}</div>`}
			</a>`;
	}

	domain_card(domain) {
		const state = this.sync_state(domain.last_status, domain.last_sync);
		return `
			<a class="ao-base ${domain.enabled ? "" : "ao-disabled"}" data-route="Form|AD Domain|${this.esc(domain.name)}">
				<div class="ao-base-head">
					<b>${this.esc(domain.name)}</b><span class="ao-badge ao-purple">AD</span>
					${domain.enabled ? "" : `<span class="ao-badge ao-gray">${__("выключен")}</span>`}
				</div>
				<div class="ao-base-title">${this.esc(domain.dns_name || domain.title || "")}</div>
				<div class="ao-base-stats">
					<span><b>${domain.enabled_accounts}</b> ${__("включённых учёток")}</span>
					<span><b>${domain.groups}</b> ${__("групп")}</span>
				</div>
				<div class="ao-sync"><span class="ao-dot ao-${state.tone}"></span><span>${__("Учётки и группы")}</span><span class="ao-muted">${this.esc(
					state.text
				)}</span></div>
			</a>`;
	}

	// ------------------------------------------------------------------ employee / account

	open_person(person) {
		this.current_request = () => this.load_card(this.api("get_person", { person }));
		this.show_tab_silently("person");
		this.current_request();
	}

	open_account(account) {
		this.current_request = () => this.load_card(this.api("get_account", { account }));
		this.show_tab_silently("person");
		this.current_request();
	}

	open_ad_account(account) {
		this.current_request = () => this.load_card(this.api("get_ad_account", { account }));
		this.show_tab_silently("person");
		this.current_request();
	}

	show_tab_silently(tab) {
		this.tab = tab;
		this.$root.find(".ao-tabs button").removeClass("active").filter(`[data-tab="${tab}"]`).addClass("active");
	}

	load_card(promise) {
		this.$view.html(this.loading());
		promise.then((data) => {
			this.current = data;
			if (this.tab === "person") this.render_card(data);
		});
	}

	render_card(data) {
		const p = data.person;
		const accounts = data.accounts || [];
		const ad_accounts = data.ad_accounts || [];
		const lone_ad = !p && !accounts.length && ad_accounts[0];
		const header = p
			? `
			<section class="ao-card ao-person">
				<div class="ao-avatar">${this.esc(this.initials(p.full_name))}</div>
				<div class="ao-person-main">
					<h2>${this.esc(p.full_name)} ${this.status_pill(p.status)}
						${p.presence === "Длительное отсутствие" ? `<span class="ao-pill ao-purple">${__("длительное отсутствие")}</span>` : ""}
						${p.external_part_time_only ? `<span class="ao-pill ao-orange">${__("только совместительство")}</span>` : ""}
					</h2>
					<div class="ao-person-meta">
						${p.position ? `<span>${this.esc(p.position)}</span>` : ""}
						${p.department ? `<span>${this.esc(p.department)}</span>` : ""}
						${p.organization ? `<span>${this.esc(p.organization)}</span>` : ""}
					</div>
				</div>
				<div class="ao-person-actions">
					<a class="btn btn-default btn-sm" data-route="Form|Person|${this.esc(p.name)}">${__("Карточка")}</a>
				</div>
			</section>`
			: lone_ad
			? `<section class="ao-card ao-person"><div class="ao-avatar ao-avatar-ad">AD</div><div class="ao-person-main"><h2>${this.esc(
					lone_ad.display_name
			  )}</h2><div class="ao-person-meta"><span>${__("Учётка AD не привязана к сотруднику")}</span><span>${this.esc(
					lone_ad.person_link_note || ""
			  )}</span></div></div></section>`
			: `<section class="ao-card ao-person"><div class="ao-avatar ao-avatar-account">1С</div><div class="ao-person-main"><h2>${this.esc(
					(accounts[0] || {}).user_name || ""
			  )}</h2><div class="ao-person-meta"><span>${__("Пользователь 1С не привязан к сотруднику")}</span><span>${this.esc(
					(accounts[0] || {}).person_link_note || ""
			  )}</span></div></div></section>`;

		const not_working = p && p.status !== "Работает" && accounts.some((a) => a.login_allowed && !a.invalid);
		const ad_not_working = p && p.status !== "Работает" && ad_accounts.some((a) => a.enabled && !a.missing_in_source);
		const alert = [
			not_working ? __("Сотрудник не работает, но вход в 1С разрешён. Учётные записи 1С ниже нужно отключить.") : "",
			ad_not_working ? __("Сотрудник не работает, но учётка AD включена.") : "",
		]
			.filter(Boolean)
			.map((text) => `<div class="ao-alert">${text}</div>`)
			.join("");

		const employments = (data.employments || [])
			.map(
				(e) => `
			<div class="ao-emp ${["Работает", "Увольняется"].includes(e.status) ? "" : "ao-emp-past"}">
				<div class="ao-emp-head"><b>${this.esc(e.position || "—")}</b>${this.status_pill(e.status)}</div>
				<div class="ao-muted">${this.esc([e.department, e.organization].filter(Boolean).join(" · "))}</div>
				<div class="ao-emp-foot">
					<span>${this.esc(e.employment_kind || "")}</span>
					<span>${this.esc(e.source)} · ${this.esc(e.tab_number || "")}</span>
					<span>${e.hire_date ? __("с") + " " + this.date(e.hire_date) : ""}${
					e.termination_date ? " " + __("по") + " " + this.date(e.termination_date) : ""
				}</span>
				</div>
			</div>`
			)
			.join("");

		const events = (data.events || [])
			.map(
				(ev) => `
			<div class="ao-event"><span class="ao-dot ${ev.processed ? "ao-gray" : "ao-blue"}"></span>
				<div><b>${this.esc(ev.event_type)}</b> <span class="ao-muted">${this.date(ev.event_date)}</span>
				${ev.details ? `<small>${this.esc(ev.details)}</small>` : ""}</div></div>`
			)
			.join("");

		this.$view.html(`
			${header}
			${alert}
			<div class="ao-grid">
				<div class="ao-span-2">
					${
						ad_accounts.length || p
							? `<h3 class="ao-section-title">${__("Active Directory")} <span class="ao-count">${ad_accounts.length}</span></h3>
					<div class="ao-accounts">${
						ad_accounts.map((a) => this.ad_account_card(a, p)).join("") || this.empty(__("Учётка AD не найдена"))
					}</div>`
							: ""
					}
					<h3 class="ao-section-title">${__("Учётные записи 1С")} <span class="ao-count">${accounts.length}</span></h3>
					<div class="ao-accounts">${accounts.map((a) => this.account_card(a, p)).join("") || this.empty(__("В базах 1С учётных записей нет"))}</div>
				</div>
				${
					p
						? `<div>
					<section class="ao-card"><header><h3>${__("Трудоустройства")}</h3></header>${employments || this.empty(__("Нет"))}</section>
					<section class="ao-card"><header><h3>${__("Кадровые события")}</h3></header>${events || this.empty(__("Событий нет"))}</section>
				</div>`
						: ""
				}
			</div>
		`);
	}

	ad_account_card(a, person) {
		const danger = person && person.status !== "Работает" && a.enabled && !a.missing_in_source;
		const groups = a.groups
			.map((g) => `<a class="ao-tag ao-blue" data-route="Form|AD Group|${this.esc(g.group)}">${this.esc(g.name)}</a>`)
			.join("");
		const number = !a.person
			? ""
			: a.employee_number_ok
			? `<span class="ao-ok-inline">✓ ${__("совпадает с сотрудником")}</span>`
			: `<span class="ao-warn-inline">${a.employee_number ? this.esc(a.employee_number) : __("не заполнен")}</span>`;
		return `
			<article class="ao-account ao-account-ad ${danger ? "ao-account-danger" : ""} ${
			a.enabled && !a.missing_in_source ? "" : "ao-account-off"
		}">
				<div class="ao-account-head">
					<b>${this.esc(a.domain)}</b><span class="ao-badge ao-purple">AD</span>
					${this.ad_badge(a)}
					${a.locked ? `<span class="ao-pill ao-orange">${__("заблокирована")}</span>` : ""}
				</div>
				<div class="ao-account-name"><a data-route="Form|AD Account|${this.esc(a.name)}">${this.esc(a.display_name)}</a></div>
				<dl class="ao-kv">
					<dt>${__("Логин")}</dt><dd>${this.esc(a.sam_account_name || "—")}</dd>
					<dt>${__("Последний вход")}</dt><dd>${this.when(a.last_logon)}</dd>
					<dt>${__("Пароль")}</dt><dd>${a.password_last_set ? __("сменён") + " " + this.when(a.password_last_set) : "—"}${
			a.password_never_expires ? ` <span class="ao-warn-inline">${__("не истекает")}</span>` : ""
		}</dd>
					<dt>OU</dt><dd>${this.esc(a.ou || "—")}</dd>
					${a.title || a.department ? `<dt>${__("В AD указано")}</dt><dd>${this.esc([a.title, a.department].filter(Boolean).join(" · "))}</dd>` : ""}
					<dt>employeeNumber</dt><dd>${number || "—"}</dd>
					<dt>${__("Сотрудник")}</dt><dd>${this.esc(
						a.person_link_method ? __("найден: {0}", [a.person_link_method]) : a.person_link_note || "—"
					)}</dd>
				</dl>
				<div class="ao-extra ao-groups"><span>${__("Группы")} (${a.groups.length})</span>${groups || `<span class="ao-muted">${__("нет")}</span>`}</div>
			</article>`;
	}

	account_card(a, person) {
		const danger = person && person.status !== "Работает" && a.login_allowed && !a.invalid;
		const profiles = (a.profiles || [])
			.map(
				(pr) => `
			<div class="ao-profile">
				<div class="ao-profile-head"><span class="ao-caret">▸</span><b>${this.esc(pr.profile)}</b>
					${pr.group && pr.group !== pr.profile ? `<span class="ao-muted">${__("через")} ${this.esc(pr.group)}</span>` : ""}
					<span class="ao-profile-orgs">${this.esc(pr.orgs || "")}</span></div>
				<div class="ao-profile-body">${(pr.restrictions || []).map((r) => `<div>${this.esc(r)}</div>`).join("")}</div>
			</div>`
			)
			.join("");
		return `
			<article class="ao-account ${danger ? "ao-account-danger" : ""} ${a.login_allowed && !a.invalid ? "" : "ao-account-off"}">
				<div class="ao-account-head">
					<b>${this.esc(a.base_code)}</b>${this.config_badge(a.base_configuration)}
					<span class="ao-login-state ${a.login_allowed && !a.invalid ? "on" : "off"}">${
					a.login_allowed && !a.invalid ? __("вход разрешён") : __("вход закрыт")
				}</span>
				</div>
				<div class="ao-account-name"><a data-route="Form|IB User|${this.esc(a.name)}">${this.esc(a.user_name)}</a></div>
				<dl class="ao-kv">
					<dt>${__("Логин")}</dt><dd>${this.esc(a.login || "—")}</dd>
					<dt>${__("Логин AD")}</dt><dd>${this.esc(a.ad_login || "—")}${
						a.ad_state === "off"
							? ` <span class="ao-warn-inline">${__("учётка отключена")}</span>`
							: a.ad_state === "missing"
							? ` <span class="ao-warn-inline">${__("нет в AD")}</span>`
							: a.ad_login && !a.ad_state
							? ` <span class="ao-muted">${__("не найдена в AD")}</span>`
							: ""
					}</dd>
					<dt>${__("Организации")}</dt><dd>${this.esc(a.orgs_text || "—")}</dd>
					<dt>${__("Сотрудник")}</dt><dd>${this.esc(
						a.person_link_method ? __("найден: {0}", [a.person_link_method]) : a.person_link_note || "—"
					)}</dd>
				</dl>
				${a.is_orphan ? `<div class="ao-tag ao-orange">${__("пользователь ИБ без карточки")}</div>` : ""}
				${
					a.extra_roles && a.extra_roles.length
						? `<div class="ao-extra"><span>${__("Роли в обход профилей")}</span>${a.extra_roles
								.map((r) => `<span class="ao-tag ao-red">${this.esc(r)}</span>`)
								.join("")}</div>`
						: ""
				}
				<div class="ao-profiles">${profiles || `<div class="ao-muted">${__("Профилей нет")}</div>`}</div>
			</article>`;
	}

	// ------------------------------------------------------------------ matrix

	render_matrix() {
		this.$view.html(`
			<div class="ao-matrix-bar">
				<select class="form-control ao-org"><option value="">${__("Все организации")}</option></select>
				<label class="ao-toggle"><input type="checkbox" class="ao-working" ${
					this.matrix_filters.only_working ? "checked" : ""
				}/> ${__("Только работающие")}</label>
				<span class="ao-legend"><span class="ao-cell-dot ao-cell-ad"></span>${__("учётка AD (число групп)")}
					<span class="ao-cell-dot"></span>${__("есть доступ (число профилей)")}
					<span class="ao-cell-dot ao-cell-extra"></span>${__("роли в обход профилей")}</span>
			</div>
			<div class="ao-matrix-wrap">${this.loading()}</div>
		`);
		this.api("get_organizations").then((orgs) => {
			const $select = this.$view.find(".ao-org");
			orgs.forEach((o) => $select.append(`<option value="${this.esc(o.name)}">${this.esc(o.title)}</option>`));
			$select.val(this.matrix_filters.organization);
		});
		this.$view.find(".ao-org").on("change", (e) => {
			this.matrix_filters.organization = $(e.currentTarget).val();
			this.load_matrix();
		});
		this.$view.find(".ao-working").on("change", (e) => {
			this.matrix_filters.only_working = $(e.currentTarget).is(":checked") ? 1 : 0;
			this.load_matrix();
		});
		this.load_matrix();
	}

	load_matrix() {
		const $wrap = this.$view.find(".ao-matrix-wrap").html(this.loading());
		this.api("get_matrix", this.matrix_filters).then((data) => {
			if (!data.rows.length) {
				$wrap.html(this.empty(__("Нет сотрудников с учётными записями 1С по этим условиям.")));
				return;
			}
			const domains = data.domains || [];
			const head =
				domains.map((d) => `<th><div>${this.esc(d.name)}</div><span class="ao-badge ao-purple">AD</span></th>`).join("") +
				data.bases.map((b) => `<th><div>${this.esc(b.name)}</div>${this.config_badge(b.configuration)}</th>`).join("");
			let group = null;
			const body = data.rows
				.map((row) => {
					let group_row = "";
					if (row.organization !== group) {
						group = row.organization;
						group_row = `<tr class="ao-group"><td colspan="${domains.length + data.bases.length + 1}">${this.esc(
							group || __("Без организации")
						)}</td></tr>`;
					}
					const ad_cells = domains
						.map((d) => {
							const c = row.ad[d.name];
							if (!c) return `<td class="ao-cell-none">·</td>`;
							return `<td><a class="ao-cell ao-cell-ad ${c.enabled ? "" : "ao-cell-off"}" title="${this.esc(
								c.enabled ? __("включена, групп: {0}", [c.groups]) : __("отключена")
							)}" data-ad-account="${this.esc(c.account)}">${c.enabled ? c.groups : "×"}</a></td>`;
						})
						.join("");
					const cells = data.bases
						.map((b) => {
							const c = row.cells[b.name];
							if (!c) return `<td class="ao-cell-none">·</td>`;
							const title = c.profiles.join("\n") || __("без профилей");
							return `<td><a class="ao-cell ${c.extra ? "ao-cell-extra" : ""}" title="${this.esc(title)}" data-account="${this.esc(
								c.account
							)}">${c.profiles.length}</a></td>`;
						})
						.join("");
					return `${group_row}<tr>
						<td class="ao-matrix-person"><a data-person="${this.esc(row.person)}"><b>${this.esc(row.full_name)}</b></a>
							${row.status !== "Работает" ? this.status_pill(row.status) : ""}
							<small>${this.esc([row.position, row.department].filter(Boolean).join(" · "))}</small></td>
						${ad_cells}${cells}</tr>`;
				})
				.join("");
			$wrap.html(`
				<div class="ao-muted ao-matrix-count">${__("Сотрудников")}: ${data.rows.length}</div>
				<table class="ao-matrix"><thead><tr><th>${__("Сотрудник")}</th>${head}</tr></thead><tbody>${body}</tbody></table>
			`);
		});
	}
}
