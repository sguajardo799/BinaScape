"""Errors that the worker reports as deterministic ``invalid_input`` failures."""


class InvalidRenderInput(ValueError):
    """The snapshot cannot be rendered by this backend; retrying cannot help."""
