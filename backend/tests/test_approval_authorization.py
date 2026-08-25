from app.core.authorization import has_permission


def test_approval_role_matrix_uses_least_privilege() -> None:
    for role in (
        "viewer",
        "analyst",
    ):
        assert not has_permission(
            role,
            "approval:read",
        )
        assert not has_permission(
            role,
            "approval:decide",
        )
        assert not has_permission(
            role,
            "approval:decide_sensitive",
        )

    assert has_permission(
        "manager",
        "approval:read",
    )
    assert has_permission(
        "manager",
        "approval:decide",
    )
    assert not has_permission(
        "manager",
        "approval:decide_sensitive",
    )

    for role in (
        "executive",
        "administrator",
        "developer",
    ):
        assert has_permission(
            role,
            "approval:read",
        )
        assert has_permission(
            role,
            "approval:decide",
        )
        assert has_permission(
            role,
            "approval:decide_sensitive",
        )
