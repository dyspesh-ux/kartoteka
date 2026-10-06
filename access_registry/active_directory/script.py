"""PowerShell script of an approved AD change plan.

Safety lives in the script itself, because it runs with the administrator's rights:
- without ``-Apply`` it only prints what it would do (dry run);
- every account is found by objectGUID, not by login;
- accounts with adminCount=1 (privileged, protected by AD) are skipped;
- an attribute is changed only if AD still has the value the plan was built on; an account to
  disable must still be enabled — what a person changed after the plan is not overwritten;
- before each change the old state goes into a rollback script (ad-plan-…-rollback.ps1): enable,
  move back to the old OU, return the groups, the old description and attribute values;
- the whole run is written to a transcript next to the script.
"""

from frappe.utils import format_datetime

from access_registry.active_directory.plan import DISABLE


def ps(value) -> str:
	"""A PowerShell single-quoted literal: nothing inside is expanded."""
	return "'" + str(value or "").replace("'", "''").replace("‘", "‘‘").replace("’", "’’") + "'"


def render(plan, domain) -> str:
	items = [i for i in plan.items if i.include]
	rows = []
	for i in items:
		if i.action == DISABLE:
			rows.append(
				f"\t@{{ Action = 'disable'; Guid = {ps(i.object_guid)}; Sam = {ps(i.sam_account_name)}; "
				f"Reason = {ps(i.reason)} }}"
			)
		else:
			rows.append(
				f"\t@{{ Action = 'set'; Guid = {ps(i.object_guid)}; Sam = {ps(i.sam_account_name)}; "
				f"Attribute = {ps(i.attribute)}; Before = {ps(i.before)}; After = {ps(i.after)} }}"
			)
	header = [
		f"# План изменений AD {plan.name}, домен {domain.name}",
		f"# Собран: {format_datetime(plan.creation)} ({plan.owner}); данные AD на {format_datetime(plan.data_as_of) if plan.data_as_of else '—'}",
		f"# Одобрил: {plan.approved_by} {format_datetime(plan.decided_on)}",
		f"# Отключить: {sum(1 for i in items if i.action == DISABLE)}, изменить атрибутов: {sum(1 for i in items if i.action != DISABLE)}",
		"#",
		"# Запуск: сначала без -Apply — скрипт только покажет, что сделает:",
		f"#   .\\{plan.name}.ps1",
		f"#   .\\{plan.name}.ps1 -Apply",
		"# Рядом появятся журнал (.log) и скрипт отката (-rollback.ps1).",
	]
	return (
		"\n".join(header)
		+ "\n"
		+ TEMPLATE.replace("__PLAN__", ps(plan.name))
		.replace("__SERVER__", ps(domain.dns_name or ""))
		.replace("__DISABLED_OU__", ps(domain.plan_disabled_ou or ""))
		.replace("__REMOVE_GROUPS__", "$true" if domain.plan_remove_groups else "$false")
		.replace("__ITEMS__", ",\n".join(rows))
	)


TEMPLATE = r"""#Requires -Modules ActiveDirectory
param(
	[switch]$Apply,
	[string]$Server = __SERVER__
)
$ErrorActionPreference = 'Stop'
$PlanId = __PLAN__
$DisabledOU = __DISABLED_OU__
$RemoveGroups = __REMOVE_GROUPS__
$Items = @(
__ITEMS__
)

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$base = Join-Path $PSScriptRoot "ad-plan-$PlanId-$stamp"
Start-Transcript -Path "$base.log" | Out-Null
$rollback = "$base-rollback.ps1"
$srv = @{}
if ($Server) { $srv = @{ Server = $Server } }
$on = ''
if ($Server) { $on = " -Server '" + ($Server -replace "['‘’]", '$0$0') + "'" }

function Lit([string]$s) { "'" + ($s -replace "['‘’]", '$0$0') + "'" }
function Undo([string]$line) { if ($Apply) { Add-Content -Path $rollback -Value $line -Encoding UTF8 } }

if ($Apply) {
	Set-Content -Path $rollback -Encoding UTF8 -Value @(
		"# Откат плана $PlanId, выполненного $stamp. Запускать целиком или нужные строки.",
		'#Requires -Modules ActiveDirectory',
		"`$ErrorActionPreference = 'Continue'"
	)
	Write-Host "План ${PlanId}: ВЫПОЛНЕНИЕ, изменений в плане: $($Items.Count)" -ForegroundColor Yellow
} else {
	Write-Host "План ${PlanId}: ПРОВЕРКА (ничего не меняется). Для выполнения запустите с -Apply" -ForegroundColor Cyan
}

$done = 0; $skipped = 0; $failed = 0
foreach ($i in $Items) {
	$who = "$($i.Sam)"
	try {
		$u = Get-ADUser -Identity $i.Guid @srv -Properties Enabled, adminCount, MemberOf, Description, DistinguishedName, title, department, company, employeeNumber
	} catch {
		Write-Warning "${who}: учётка не найдена по objectGUID — пропуск"; $skipped++; continue
	}
	if ($u.adminCount -eq 1) { Write-Warning "${who}: привилегированная учётка (adminCount=1) — пропуск"; $skipped++; continue }
	try {
		if ($i.Action -eq 'disable') {
			if (-not $u.Enabled) { Write-Host "${who}: уже отключена — пропуск"; $skipped++; continue }
			$parent = ($u.DistinguishedName -split '(?<!\\),', 2)[1]
			$groups = @($u.MemberOf)
			$note = "Отключена $(Get-Date -Format 'dd.MM.yyyy') по плану $PlanId"
			if (-not $Apply) {
				Write-Host "[проверка] ${who}: отключить ($($i.Reason))$(if ($RemoveGroups) { ", снять групп: $($groups.Count)" })$(if ($DisabledOU) { ", перенести в $DisabledOU" })"
				continue
			}
			Undo "# ${who}"
			Undo "Enable-ADAccount -Identity $(Lit $i.Guid)$on"
			if ($u.Description) { Undo "Set-ADUser -Identity $(Lit $i.Guid) -Description $(Lit $u.Description)$on" }
			else { Undo "Set-ADUser -Identity $(Lit $i.Guid) -Clear description$on" }
			Disable-ADAccount -Identity $u @srv
			Set-ADUser -Identity $u -Description ((@($note, $u.Description) | Where-Object { $_ }) -join '. ') @srv
			if ($RemoveGroups) {
				foreach ($g in $groups) {
					Undo "Add-ADGroupMember -Identity $(Lit $g) -Members $(Lit $i.Guid)$on"
					Remove-ADGroupMember -Identity $g -Members $u -Confirm:$false @srv
				}
			}
			if ($DisabledOU -and $parent -ne $DisabledOU) {
				Undo "Move-ADObject -Identity $(Lit $i.Guid) -TargetPath $(Lit $parent)$on"
				Move-ADObject -Identity $u.ObjectGUID -TargetPath $DisabledOU @srv
			}
			Write-Host "${who}: отключена" -ForegroundColor Green
		} else {
			$current = "$($u.($i.Attribute))".Trim()
			if ($current -ne $i.Before) {
				Write-Warning "${who}: $($i.Attribute) = '$current', а план собран для '$($i.Before)' — кто-то уже поменял, пропуск"; $skipped++; continue
			}
			if (-not $Apply) { Write-Host "[проверка] ${who}: $($i.Attribute): '$($i.Before)' → '$($i.After)'"; continue }
			if ($i.Before) {
				Undo "Set-ADUser -Identity $(Lit $i.Guid) -Replace @{ $($i.Attribute) = $(Lit $i.Before) }$on"
			} else {
				Undo "Set-ADUser -Identity $(Lit $i.Guid) -Clear $($i.Attribute)$on"
			}
			Set-ADUser -Identity $u -Replace @{ ($i.Attribute) = $i.After } @srv
			Write-Host "${who}: $($i.Attribute) → '$($i.After)'" -ForegroundColor Green
		}
		$done++
	} catch {
		Write-Warning "${who}: ошибка — $($_.Exception.Message)"; $failed++
	}
}

if ($Apply) {
	Write-Host "Готово: выполнено $done, пропущено $skipped, ошибок $failed. Откат: $rollback" -ForegroundColor Yellow
} else {
	Write-Host "Проверка закончена: пропущено бы $skipped. Ничего не изменено." -ForegroundColor Cyan
}
Stop-Transcript | Out-Null
"""
