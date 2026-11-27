"""Role -> permission map. Must match the seed in db/migrations/0001_initial.sql (tested)."""
ROLE_PERMISSIONS = {
    "user": {"translate.use", "history.own", "signs.propose"},
    "contributor": {"samples.upload_own"},
    "annotator": {"samples.review_consented"},
    "admin": {"signs.review", "users.manage", "reports.manage", "dataset.manage",
              "models.manage", "models.publish", "audit.read"},
    "superadmin": {"system.manage", "roles.manage"},
}


def permissions_for(roles) -> set:
    out = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out


def has_permission(roles, permission: str) -> bool:
    return permission in permissions_for(roles)
