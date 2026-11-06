"""What gets added to a terminal — the rules, tested without any I/O."""

from __future__ import annotations

from app.biometric.base import DeviceUserRecord
from app.hrms.client import HrmsEmployee
from app.services.enrollment_service import normalize_card, plan_additions


def emp(code: str, name: str = "Some One", card: str | None = None) -> HrmsEmployee:
    return HrmsEmployee(employee_code=code, name=name, card_number=card)


def rec(uid: int, code: str, card: str | None = None) -> DeviceUserRecord:
    return DeviceUserRecord(uid=uid, user_id=code, name="x", card=card)


class TestWhoIsNew:
    def test_only_users_missing_from_the_terminal_are_added(self) -> None:
        plan = plan_additions([emp("IQ-1"), emp("IQ-2"), emp("IQ-3")], [rec(1, "IQ-2")])

        assert [p.employee_code for p in plan.to_add] == ["IQ-1", "IQ-3"]
        assert plan.already_present == 1

    def test_matching_ignores_case_and_surrounding_space(self) -> None:
        plan = plan_additions([emp(" iq-1004 ")], [rec(1, "IQ-1004")])

        assert plan.to_add == []
        assert plan.already_present == 1

    def test_an_employee_listed_twice_is_added_once(self) -> None:
        plan = plan_additions([emp("IQ-1"), emp("iq-1")], [])

        assert len(plan.to_add) == 1

    def test_nothing_to_do_when_everyone_is_present(self) -> None:
        plan = plan_additions([emp("IQ-1")], [rec(1, "IQ-1")])

        assert plan.to_add == []
        assert plan.skipped == []


class TestSlots:
    def test_never_picks_an_occupied_slot(self) -> None:
        """set_user overwrites whatever holds the slot, so reusing one deletes a person."""
        existing = [rec(1, "A"), rec(2, "B"), rec(4, "C")]

        plan = plan_additions([emp("N1"), emp("N2")], existing)

        assert [p.uid for p in plan.to_add] == [3, 5]

    def test_slots_are_distinct_within_one_run(self) -> None:
        plan = plan_additions([emp("N1"), emp("N2"), emp("N3")], [])

        assert [p.uid for p in plan.to_add] == [1, 2, 3]

    def test_a_full_terminal_skips_with_a_reason(self) -> None:
        existing = [rec(uid, f"U{uid}") for uid in range(1, 65_536)]

        plan = plan_additions([emp("NEW")], existing)

        assert plan.to_add == []
        assert "no free user slot" in plan.skipped[0].reason


class TestCodeAndName:
    def test_an_overlong_code_is_skipped_not_truncated(self) -> None:
        """Truncating would register the person under a code HRMS does not know."""
        plan = plan_additions([emp("X" * 25)], [])

        assert plan.to_add == []
        assert "longer than" in plan.skipped[0].reason

    def test_a_blank_name_falls_back_to_the_code(self) -> None:
        plan = plan_additions([emp("IQ-9", name="  ")], [])

        assert plan.to_add[0].name == "IQ-9"


class TestCards:
    def test_a_valid_card_is_kept(self) -> None:
        plan = plan_additions([emp("A", card="0012345678")], [])

        assert plan.to_add[0].card == "12345678"
        assert plan.to_add[0].warnings == []

    def test_a_non_numeric_card_is_dropped_with_a_warning(self) -> None:
        plan = plan_additions([emp("A", card="AB12")], [])

        assert plan.to_add[0].card is None
        assert "not numeric" in plan.to_add[0].warnings[0]

    def test_a_card_already_on_the_terminal_is_dropped_not_duplicated(self) -> None:
        """Two people on one card would credit the wrong person."""
        plan = plan_additions([emp("A", card="555")], [rec(1, "OTHER", card="555")])

        assert plan.to_add[0].card is None
        assert "already assigned" in plan.to_add[0].warnings[0]

    def test_two_new_employees_with_one_card_only_the_first_keeps_it(self) -> None:
        plan = plan_additions([emp("A", card="9"), emp("B", card="9")], [])

        assert plan.to_add[0].card == "9"
        assert plan.to_add[1].card is None

    def test_card_normalisation(self) -> None:
        assert normalize_card(None) == (None, None)
        assert normalize_card("  ") == (None, None)
        assert normalize_card("0") == (None, None)
        assert normalize_card("007") == ("7", None)
        assert normalize_card("4294967295")[0] == "4294967295"
        assert normalize_card("4294967296")[0] is None
