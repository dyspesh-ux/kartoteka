// A PowerShell single-quoted literal: every character PowerShell takes for a single quote
// (' ‘ ’ ‚ ‛) is doubled, so names from AD cannot end the string and run as code.
const lit = (value) => "'" + String(value || "").replace(/['\u2018\u2019\u201a\u201b]/g, "$&$&") + "'";

// Copyright (c) 2026, Access Registry contributors
// For license information, please see license.txt

frappe.query_reports["AD employeeNumber"] = {
	filters: [
		{ fieldname: "domain", label: __("Домен"), fieldtype: "Link", options: "AD Domain" },
		{ fieldname: "ou", label: __("OU содержит"), fieldtype: "Data" },
		{ fieldname: "show_suppressed", label: __("Показать погашенные"), fieldtype: "Check" },
	],
	onload(report) {
		report.page.add_inner_button(__("Скачать скрипт PowerShell"), () => {
			const rows = (frappe.query_report.data || []).filter((r) => r.object_guid && r.person);
			if (!rows.length) {
				frappe.msgprint(__("Нечего проставлять"));
				return;
			}
			const lines = [
				"# Простановка employeeNumber = UUID сотрудника из реестра доступа.",
				"# Сформировано: " + frappe.datetime.now_datetime() + ", учёток: " + rows.length,
				"# Сначала запустите как есть (пробный прогон), затем с -Apply.",
				"param([switch]$Apply)",
				"Import-Module ActiveDirectory",
				"$items = @(",
				...rows.map((r, i) => `    @{ Guid = ${lit(r.object_guid)}; Uuid = ${lit(r.person)}; Name = ${lit(r.display_name)} }${i < rows.length - 1 ? "," : ""}`),
				")",
				"foreach ($i in $items) {",
				"    $u = Get-ADUser -Identity $i.Guid -Properties employeeNumber",
				"    Write-Host (\"{0}: '{1}' -> '{2}'\" -f $i.Name, $u.employeeNumber, $i.Uuid)",
				"    if ($Apply) { Set-ADUser -Identity $i.Guid -Replace @{ employeeNumber = $i.Uuid } }",
				"}",
			];
			const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/plain;charset=utf-8" });
			const a = document.createElement("a");
			a.href = URL.createObjectURL(blob);
			a.download = "set-employeeNumber.ps1";
			a.click();
		});
	},
};
