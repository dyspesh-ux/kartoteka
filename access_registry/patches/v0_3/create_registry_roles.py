from access_registry.install import create_roles


def execute():
	"""Roles must exist before the DocTypes that grant them permissions are imported."""
	create_roles()
