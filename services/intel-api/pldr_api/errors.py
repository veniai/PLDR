from __future__ import annotations


class ArchivedIntakeError(ValueError):
    """Raised when a write would revive or mutate a globally archived intake."""

    code = "intake_archived"

    def __init__(self, action: str = "continuing") -> None:
        super().__init__(
            f"This intake item is archived. Restore it before {action}."
        )


class UnlinkedReviewTaskError(ValueError):
    """Raised when a worker task no longer has its required topic membership."""

    code = "intake_not_linked"

    def __init__(self) -> None:
        super().__init__(
            "This intake item is no longer linked to the task investigation. "
            "Restore the investigation relationship before retrying."
        )


class IntakeScopeError(ValueError):
    """Raised when a scoped review loses its investigation membership."""

    code = "intake_scope_changed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "This intake item is no longer linked to this investigation. "
            "Restore the investigation relationship before confirming."
        )


class IntakeMutationConflictError(ValueError):
    """Raised when work begun from an obsolete Intake version tries to commit."""

    code = "intake_superseded"

    def __init__(self, action: str = "continuing") -> None:
        super().__init__(
            f"This intake item changed while work was running. Reopen it before {action}."
        )
