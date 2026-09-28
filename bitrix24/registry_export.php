<?php
/**
 * Access Registry: read-only export of access rights from an on-premise Bitrix24 («коробка»).
 *
 * REST API does not expose CRM role permissions, disk folder rights, user groups and the absence
 * chart, so this script reads them on the server and returns one JSON document.
 *
 * Install:
 *   1. Copy to /local/registry/export.php on the Bitrix24 web server.
 *   2. Put a long random string into REGISTRY_TOKEN below (for example: openssl rand -hex 32).
 *   3. In Access Registry → «Порталы Битрикс24»: «Адрес скрипта выгрузки» =
 *      https://b24.example.local/local/registry/export.php, «Ключ скрипта выгрузки» = the same string.
 *
 * The script only reads. It does not change anything in Bitrix24.
 * Reference implementation: check on your Bitrix24 version before production use.
 */

const REGISTRY_TOKEN = '';           // required: without a token the script answers 403
const DISK_FOLDER_DEPTH = 2;         // rights of common storages: root and folders down to this depth
const ABSENCE_DAYS_BACK = 60;        // absence chart: records that ended not earlier than N days ago

define('NO_KEEP_STATISTIC', true);
define('NOT_CHECK_PERMISSIONS', true);
define('NO_AGENT_CHECK', true);
define('STOP_STATISTICS', true);
define('BX_SECURITY_SESSION_VIRTUAL', true);

require $_SERVER['DOCUMENT_ROOT'] . '/bitrix/modules/main/include/prolog_before.php';

use Bitrix\Main\Loader;

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$token = $_SERVER['HTTP_X_REGISTRY_TOKEN'] ?? '';
if (REGISTRY_TOKEN === '' || !is_string($token) || !hash_equals(REGISTRY_TOKEN, $token)) {
	http_response_code(403);
	echo json_encode(['error' => 'forbidden']);
	exit;
}

$out = [
	'version' => 1,
	'generated_at' => date('c'),
	'logins' => [],
	'user_groups' => [],
	'crm_roles' => [],
	'crm_role_relations' => [],
	'disk_rights' => [],
	'absences' => [],
	'warnings' => [],
];

function registry_date($value)
{
	if (!$value) {
		return null;
	}
	$ts = MakeTimeStamp($value);
	return $ts ? date('Y-m-d', $ts) : null;
}

// ------------------------------------------------------------------ users: login and external auth
$by = 'id';
$order = 'asc';
$users = CUser::GetList($by, $order, [], ['FIELDS' => ['ID', 'LOGIN', 'EXTERNAL_AUTH_ID', 'XML_ID', 'ACTIVE']]);
while ($u = $users->Fetch()) {
	$out['logins'][] = [
		'user_id' => (int)$u['ID'],
		'login' => (string)$u['LOGIN'],
		'external_auth_id' => (string)$u['EXTERNAL_AUTH_ID'],
		'xml_id' => (string)$u['XML_ID'],
	];
}

// ------------------------------------------------------------------ user groups (1 — администраторы)
$by = 'c_sort';
$order = 'asc';
$groups = CGroup::GetList($by, $order, ['ACTIVE' => 'Y']);
while ($g = $groups->Fetch()) {
	$out['user_groups'][] = [
		'id' => (int)$g['ID'],
		'name' => (string)$g['NAME'],
		'string_id' => (string)$g['STRING_ID'],
		'members' => array_map('intval', CGroup::GetGroupUser($g['ID'])),
	];
}

// ------------------------------------------------------------------ CRM roles and smart processes
if (Loader::includeModule('crm')) {
	$roles = CCrmRole::GetList(['ID' => 'ASC'], []);
	while ($role = $roles->Fetch()) {
		$permissions = [];
		foreach ((array)CCrmRole::GetRolePerms($role['ID']) as $entity => $actions) {
			foreach ((array)$actions as $action => $levels) {
				$level = is_array($levels) ? ($levels['-'] ?? '') : (string)$levels;
				if ($level === '' || $level === null) {
					continue;
				}
				$permissions[] = ['entity' => (string)$entity, 'action' => (string)$action, 'level' => (string)$level];
			}
		}
		$out['crm_roles'][] = ['id' => (int)$role['ID'], 'name' => (string)$role['NAME'], 'permissions' => $permissions];
	}
	$relations = CCrmRole::GetRelation();
	while ($relation = $relations->Fetch()) {
		$out['crm_role_relations'][] = ['role_id' => (int)$relation['ROLE_ID'], 'access_code' => (string)$relation['RELATION']];
	}
} else {
	$out['warnings'][] = 'module crm is not installed';
}

// ------------------------------------------------------------------ common disk storages
if (Loader::includeModule('disk')) {
	$taskNames = [];
	$tasks = \Bitrix\Main\TaskTable::getList(['filter' => ['=MODULE_ID' => 'disk'], 'select' => ['ID', 'NAME']]);
	while ($task = $tasks->fetch()) {
		$taskNames[(int)$task['ID']] = (string)$task['NAME'];
	}
	$storages = \Bitrix\Disk\Internals\StorageTable::getList([
		'filter' => ['=ENTITY_TYPE' => \Bitrix\Disk\ProxyType\Common::className()],
		'select' => ['ID', 'NAME', 'ROOT_OBJECT_ID'],
	]);
	while ($storage = $storages->fetch()) {
		$level = [[(int)$storage['ROOT_OBJECT_ID'], '/']];
		for ($depth = 0; $depth <= DISK_FOLDER_DEPTH && $level; $depth++) {
			$next = [];
			foreach ($level as [$objectId, $path]) {
				$rights = \Bitrix\Disk\Internals\RightTable::getList([
					'filter' => ['=OBJECT_ID' => $objectId],
					'select' => ['ACCESS_CODE', 'TASK_ID', 'NEGATIVE'],
				]);
				while ($right = $rights->fetch()) {
					$out['disk_rights'][] = [
						'storage' => (string)$storage['NAME'],
						'path' => $path,
						'access_code' => (string)$right['ACCESS_CODE'],
						'task' => $taskNames[(int)$right['TASK_ID']] ?? (string)$right['TASK_ID'],
						'negative' => (bool)$right['NEGATIVE'],
					];
				}
				if ($depth < DISK_FOLDER_DEPTH) {
					$children = \Bitrix\Disk\Internals\ObjectTable::getList([
						'filter' => [
							'=PARENT_ID' => $objectId,
							'=TYPE' => \Bitrix\Disk\Internals\ObjectTable::TYPE_FOLDER,
							'=DELETED_TYPE' => 0,
						],
						'select' => ['ID', 'NAME'],
					]);
					while ($child = $children->fetch()) {
						$next[] = [(int)$child['ID'], rtrim($path, '/') . '/' . $child['NAME']];
					}
				}
			}
			$level = $next;
		}
	}
} else {
	$out['warnings'][] = 'module disk is not installed';
}

// ------------------------------------------------------------------ absence chart (iblock structure/absence)
if (Loader::includeModule('iblock')) {
	$iblock = CIBlock::GetList([], ['TYPE' => 'structure', 'CODE' => 'absence'])->Fetch();
	if ($iblock) {
		$since = ConvertTimeStamp(time() - ABSENCE_DAYS_BACK * 86400, 'SHORT');
		$elements = CIBlockElement::GetList(
			['DATE_ACTIVE_FROM' => 'ASC'],
			['IBLOCK_ID' => $iblock['ID'], '>=DATE_ACTIVE_TO' => $since],
			false,
			false,
			['ID', 'NAME', 'DATE_ACTIVE_FROM', 'DATE_ACTIVE_TO', 'PROPERTY_USER', 'PROPERTY_ABSENCE_TYPE']
		);
		while ($e = $elements->Fetch()) {
			$out['absences'][] = [
				'id' => (int)$e['ID'],
				'user_id' => (int)$e['PROPERTY_USER_VALUE'],
				'date_from' => registry_date($e['DATE_ACTIVE_FROM']),
				'date_to' => registry_date($e['DATE_ACTIVE_TO']),
				'type' => (string)($e['PROPERTY_ABSENCE_TYPE_VALUE'] ?? ''),
				'title' => (string)$e['NAME'],
			];
		}
	} else {
		$out['warnings'][] = 'absence chart iblock (structure/absence) not found';
	}
}

echo json_encode($out, JSON_UNESCAPED_UNICODE);
