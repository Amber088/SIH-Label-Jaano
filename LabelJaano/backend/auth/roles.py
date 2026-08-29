"""
Roles and permissions.

Label Jaano has two real personas and one operational one:

* **consumer** — a shopper who scans a product they are about to buy. Sees only
  their own scans. This is the crowdsourced-reporting half of the idea: thousands
  of consumers photographing labels is coverage no inspectorate could staff.
* **officer** — a Legal Metrology / FSSAI inspector. Sees the whole corpus, which
  is what makes the aggregate view ("which declaration is breached most often")
  useful for planning enforcement.
* **admin** — may hot-reload the rule packs. Reloading is how a gazette amendment
  reaches production, so it changes what every future verdict means and must not
  be something an ordinary account can trigger.

Permissions are declared as data, in one table below, rather than as scattered
``if role == "officer"`` branches. The API asks
``role.can(Permission.VIEW_ALL_SCANS)`` and never inspects the role directly, so
adding a role means adding a row here.
"""
from __future__ import annotations

from enum import Enum

__all__ = ["Role", "Permission", "DEFAULT_ROLE"]


class Permission(str, Enum):
    VIEW_ALL_SCANS = "view_all_scans"        # the officer queue across every user
    VIEW_AGGREGATE_STATS = "view_aggregate_stats"
    DELETE_ANY_SCAN = "delete_any_scan"      # beyond one's own history
    RELOAD_RULEPACKS = "reload_rulepacks"    # push a gazette amendment live
    MANAGE_USERS = "manage_users"


class Role(str, Enum):
    CONSUMER = "consumer"
    OFFICER = "officer"
    ADMIN = "admin"

    @classmethod
    def parse(cls, value: object, *, default: "Role | None" = None) -> "Role":
        """Lenient parse for values arriving from the DB or a token claim.

        Unrecognised input degrades to the *least* privileged role rather than
        raising — a corrupted role column must never accidentally grant access.
        """
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for role in cls:
            if role.value == text:
                return role
        return default or cls.CONSUMER

    @property
    def label(self) -> str:
        return {
            Role.CONSUMER: "Consumer",
            Role.OFFICER: "Enforcement officer",
            Role.ADMIN: "Administrator",
        }[self]

    def can(self, permission: Permission) -> bool:
        return permission in _PERMISSIONS[self]


DEFAULT_ROLE = Role.CONSUMER

# Self-registration yields a consumer unless the caller proves they are staff by
# presenting the value of ``LABEL_JAANO_OFFICER_CODE`` (see auth.registration).
# "Anyone who signs up can read every inspection in the district" is not an
# acceptable default, so the privileged roles are never freely choosable, and
# admin accounts are only ever created out-of-band by ``manage.py``.
_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CONSUMER: frozenset(),
    Role.OFFICER: frozenset(
        {
            Permission.VIEW_ALL_SCANS,
            Permission.VIEW_AGGREGATE_STATS,
        }
    ),
    Role.ADMIN: frozenset(Permission),  # every permission, present and future
}
