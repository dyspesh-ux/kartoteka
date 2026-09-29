# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.file_shares.reports import share_issues


def execute(filters=None):
	return share_issues(filters)
