"""How an unconfigured HRMS is reported to the operator.

The status and the message are the only parts of a failed run most people read, so
both have to point at the thing that is actually wrong.
"""

from __future__ import annotations

from app.core.enums import SyncStatus
from app.core.errors import HrmsNotConfiguredError


class TestStatus:
    def test_it_is_an_hrms_status_not_a_device_one(self) -> None:
        """Regression: this used to report DEVICE_CONFIGURATION_ERROR.

        A missing API key would blame a terminal that was working perfectly, and send
        the operator to the Devices page to debug hardware instead of to the HRMS page
        to finish the configuration.
        """
        assert HrmsNotConfiguredError("x").status is SyncStatus.HRMS_NOT_CONFIGURED
        assert HrmsNotConfiguredError("x").status is not SyncStatus.DEVICE_CONFIGURATION_ERROR

    def test_it_is_not_retryable(self) -> None:
        """Nothing about resending fixes a blank configuration field."""
        assert HrmsNotConfiguredError("x").retryable is False

    def test_it_is_not_counted_as_success(self) -> None:
        assert SyncStatus.HRMS_NOT_CONFIGURED.is_success is False
        assert SyncStatus.HRMS_NOT_CONFIGURED.is_terminal is True
