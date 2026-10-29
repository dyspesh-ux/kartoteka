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

import base64
import json

from frappe.utils import format_datetime

from access_registry.active_directory.plan import DISABLE

# every character PowerShell takes for a single quote: ' ‘ ’ ‚ ‛
QUOTES = "'\u2018\u2019\u201a\u201b"


def ps(value) -> str:
	"""A PowerShell single-quoted literal: nothing inside is expanded, every quote doubled."""
	text = str(value or "")
	for q in QUOTES:
		text = text.replace(q, q + q)
	return "'" + text + "'"


ATTRIBUTES = {"title", "department", "company", "employeeNumber"}


def payload(data) -> str:
	"""Data of the plan as Base64 of JSON: values from ZUP and AD never become PowerShell code."""
	return base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")


def render(plan, domain) -> str:
	items = [i for i in plan.items if i.include]
	unknown = {i.attribute for i in items if i.action != DISABLE and i.attribute not in ATTRIBUTES}
	if unknown:  # only attributes the registry plans; anything else means the plan was tampered with
		raise ValueError(f"unexpected attributes in the plan: {sorted(unknown)}")
	rows = []
	for i in items:
		if i.action == DISABLE:
			rows.append(
				{
					"Action": "disable",
					"Guid": i.object_guid,
					"Sam": i.sam_account_name,
					"Reason": i.reason or "",
				}
			)
		else:
			rows.append(
				{
					"Action": "set",
					"Guid": i.object_guid,
					"Sam": i.sam_account_name,
					"Attribute": i.attribute,
					"Before": i.before or "",
					"After": i.after or "",
				}
			)
	# the settings the plan was approved with, not today's domain card (older plans: the card)
	settings = {
		"PlanId": plan.name,
		"Server": getattr(plan, "server", None) or domain.dns_name or "",
		"DisabledOU": getattr(plan, "disabled_ou", None)
		if getattr(plan, "settings_saved", None)
		else (domain.plan_disabled_ou or ""),
		"RemoveGroups": bool(
			getattr(plan, "remove_groups", None)
			if getattr(plan, "settings_saved", None)
			else domain.plan_remove_groups
		),
		"Items": rows,
	}
	header = [
		f"# План изменений AD {plan.name}, домен {domain.name}",
		f"# Собран: {format_datetime(plan.creation)} ({plan.owner}); данные AD на {format_datetime(plan.data_as_of) if plan.data_as_of else '—'}",
		f"# Одобрил: {plan.approved_by} {format_datetime(plan.decided_on)}",
		f"# Отключить: {sum(1 for i in items if i.action == DISABLE)}, изменить атрибутов: {sum(1 for i in items if i.action != DISABLE)}",
		"# Данные плана — в $Payload (Base64 от JSON): посмотреть их можно без запуска, раскодировав строку.",
		"#",
		"# Запуск: сначала без -Apply — скрипт только покажет, что сделает:",
		f"#   .\\{plan.name}.ps1",
		f"#   .\\{plan.name}.ps1 -Apply",
		"# Рядом появятся журнал (.log) и скрипт отката (-rollback.ps1).",
	]
	return "\n".join(header) + "\n" + TEMPLATE.replace("__PAYLOAD__", payload(settings))


TEMPLATE = r"""#Requires -Modules ActiveDirectory
param(
	[switch]$Apply,
	[string]$Server
)
$ErrorActionPreference = 'Stop'
$Payload = '__PAYLOAD__'
$Plan = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
$PlanId = $Plan.PlanId
if (-not $Server) { $Server = $Plan.Server }
$DisabledOU = $Plan.DisabledOU
$RemoveGroups = [bool]$Plan.RemoveGroups
$Items = @($Plan.Items)

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$base = Join-Path $PSScriptRoot "ad-plan-$PlanId-$stamp"
Start-Transcript -Path "$base.log" | Out-Null
$rollback = "$base-rollback.ps1"
$srv = @{}
if ($Server) { $srv = @{ Server = $Server } }
# a literal for the rollback script: every character PowerShell takes for a single quote is doubled
function Lit([string]$s) { "'" + ($s -replace "['\u2018\u2019\u201a\u201b]", '$0$0') + "'" }
$on = ''
if ($Server) { $on = " -Server " + (Lit $Server) }
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
