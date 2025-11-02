"""Custom test runner configuration for the project."""

from django.test.runner import DiscoverRunner


class NonInteractiveDiscoverRunner(DiscoverRunner):
    """Force non-interactive test execution to auto-clobber test databases."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure Django never prompts for input when reusing the test database.
        self.interactive = False
