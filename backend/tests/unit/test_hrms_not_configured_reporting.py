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


class TestAddressingErrors:
    """A 404 means the URL is wrong, and the message has to say so."""

    def test_a_404_names_the_url_instead_of_pasting_the_error_page(self) -> None:
        import httpx
        import pytest

        from app.core.errors import HrmsBadRequestError
        from app.hrms.client import HrmsClient
        from app.hrms.profile import HrmsProfile

        client = HrmsClient(
            base_url="http://localhost:5200",
            profile=HrmsProfile(attendance_path="/api/v1/attendence/biometric"),
        )
        response = httpx.Response(
            404,
            html="<html><body><pre>Cannot POST /api/v1/attendence/biometric</pre></body></html>",
            request=httpx.Request("POST", "http://localhost:5200"),
        )

        with pytest.raises(HrmsBadRequestError) as caught:
            client._interpret(response, [], 5)  # noqa: SLF001

        message = caught.value.message
        assert "http://localhost:5200/api/v1/attendence/biometric" in message
        assert "base URL" in message
        # The server's HTML must not reach the operator-facing message.
        assert "<html>" not in message and "Cannot POST" not in message
