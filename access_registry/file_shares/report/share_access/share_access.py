# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.file_shares.reports import share_access


def execute(filters=None):
	return share_access(filters)
