# Copyright (c) 2026, Access Registry contributors
# For license information, please see license.txt

from access_registry.access_roles.reports import review_results


def execute(filters=None):
	return review_results(filters)
