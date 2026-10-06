# Fake ActiveDirectory module for test_ad_plan_script: state in a JSON file, every call logged.
$global:StatePath = $env:FAKE_AD_STATE
function Load { Get-Content $global:StatePath -Raw | ConvertFrom-Json -AsHashtable }
function Save($s) { $s | ConvertTo-Json -Depth 5 | Set-Content $global:StatePath }
function Log($m) { Add-Content $env:FAKE_AD_LOG $m }
function GuidOf($id) { if ($id -is [string]) { return $id }; return "$($id.ObjectGUID)" }
function Get-ADUser { param($Identity, $Server, $Properties) $s = Load; $u = $s.users[(GuidOf $Identity)]; if (-not $u) { throw "not found" }; [pscustomobject]$u }
function Disable-ADAccount { param($Identity, $Server) $g = GuidOf $Identity; $s = Load; $s.users[$g].Enabled = $false; Save $s; Log "Disable $g" }
function Enable-ADAccount { param($Identity, $Server) $g = GuidOf $Identity; $s = Load; $s.users[$g].Enabled = $true; Save $s; Log "Enable $g" }
function Set-ADUser { param($Identity, $Server, $Description, $Replace, $Clear)
	$g = GuidOf $Identity; $s = Load; $u = $s.users[$g]
	if ($PSBoundParameters.ContainsKey('Description')) { $u.Description = $Description; Log "SetDesc $g = $Description" }
	if ($Replace) { foreach ($k in $Replace.Keys) { $u[$k] = $Replace[$k]; Log "Replace $g $k = $($Replace[$k])" } }
	if ($Clear) { foreach ($k in @($Clear)) { $u[$k] = $null; Log "Clear $g $k" } }
	Save $s }
function Remove-ADGroupMember { param($Identity, $Members, $Server, [switch]$Confirm) $g = GuidOf $Members; $s = Load; $u = $s.users[$g]; $u.MemberOf = @($u.MemberOf | Where-Object { $_ -ne $Identity }); Save $s; Log "RemoveGroup $g $Identity" }
function Add-ADGroupMember { param($Identity, $Members, $Server) $g = GuidOf $Members; $s = Load; $u = $s.users[$g]; $u.MemberOf = @($u.MemberOf) + $Identity; Save $s; Log "AddGroup $g $Identity" }
function Move-ADObject { param($Identity, $TargetPath, $Server) $g = GuidOf $Identity; $s = Load; $u = $s.users[$g]; $cn = ($u.DistinguishedName -split '(?<!\\),', 2)[0]; $u.DistinguishedName = "$cn,$TargetPath"; Save $s; Log "Move $g -> $TargetPath" }
Export-ModuleMember -Function *-AD*
