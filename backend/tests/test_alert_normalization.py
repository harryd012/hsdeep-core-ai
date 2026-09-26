"""
Tests for provider-text normalization and the Alert plain-text contract.

The real defect this covers, verified in the live development database:
177 of 1102 alerts store raw PRTG markup in `Alert.description`, of the shape
``<div class="logmessage">Requested by PRTG System Administrator<div class="moreicon"></div></div>``.
The Alerts UI must render plain text; raw provider payload is only for
"Details -> Raw Event".
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.text import (
    contains_markup,
    normalize_provider_text,
    plain_text,
)
from app.schemas.alert import AlertOut

#: The exact shape observed in the live database.
PRTG_LOG_MESSAGE = (
    '<div class="logmessage">Requested by PRTG System Administrator'
    '<div class="moreicon"></div></div>'
)


class TestRealWorldPayloads:

    def test_prtg_logmessage_becomes_plain_text(self):
        result = normalize_provider_text(PRTG_LOG_MESSAGE)
        assert result.text == "Requested by PRTG System Administrator"
        assert result.had_markup is True
        assert "<div" not in result.text
        assert "logmessage" not in result.text

    def test_plain_text_is_returned_byte_for_byte(self):
        """Clean values must never be altered (no entity/whitespace mangling)."""
        for value in (
            "Disk Free",
            "Port 443 unreachable",
            "C:\\Temp\\file.txt",
            "a & b",
        ):
            result = normalize_provider_text(value)
            assert result.text == value, value
            assert result.had_markup is False, value


class TestTagStripping:

    def test_nested_tags_are_removed(self):
        assert plain_text("<div><span><b>Core</b></span></div>") == "Core"

    def test_br_and_block_tags_become_line_breaks(self):
        text = plain_text("Line one<br>Line two</p><p>Line three")
        assert "Line one" in text
        assert "Line two" in text
        assert "Line three" in text

    def test_script_content_is_dropped_not_just_unwrapped(self):
        result = normalize_provider_text("<div>ok</div><script>alert(1)</script>")
        assert result.text == "ok"
        assert "alert" not in result.text
        assert result.dropped_active_content is True

    def test_style_content_is_dropped(self):
        result = normalize_provider_text("<style>.x{color:red}</style>visible")
        assert "color" not in (result.text or "")
        assert "visible" in (result.text or "")

    def test_escaped_markup_never_becomes_a_live_tag(self):
        """`&lt;script&gt;` must not be "helpfully" decoded into a real tag -
        doing so would CREATE the XSS vector this module exists to prevent."""
        text = plain_text("&lt;script&gt;")
        assert text == "&lt;script&gt;"
        assert "<" not in text
        assert ">" not in text

    def test_entities_are_decoded_and_angle_brackets_are_never_emitted(self):
        assert plain_text("<p>Tom&nbsp;&amp;&nbsp;Jerry</p>") == "Tom & Jerry"
        # Numeric and named angle forms stay escaped.
        assert plain_text("<p>5 &lt; 10 &gt; 2</p>") == "5 &lt; 10 &gt; 2"
        assert plain_text("<p>&#60;script&#62;</p>") == "&lt;script&gt;"

    def test_attributes_do_not_leak_into_text(self):
        text = plain_text('<img src="http://evil/x.png" onerror="alert(1)">Done')
        assert "onerror" not in (text or "")
        assert "evil" not in (text or "")
        assert text == "Done"

    def test_malformed_markup_does_not_raise(self):
        for value in ("<div", "a < b > c", "<>", "</>", "<div class=>x</div>"):
            normalize_provider_text(value)  # must not raise

    def test_whitespace_is_collapsed(self):
        assert plain_text("<div>  a\t\t b   </div>") == "a b"


class TestEdges:

    def test_none_in_none_out(self):
        assert normalize_provider_text(None).text is None
        assert plain_text(None) is None

    def test_empty_string_stays_empty(self):
        assert normalize_provider_text("").text == ""
        assert normalize_provider_text("   ").text == ""

    def test_markup_only_string_becomes_empty(self):
        assert plain_text("<div></div>") == ""

    def test_contains_markup_helper(self):
        assert contains_markup(PRTG_LOG_MESSAGE) is True
        assert contains_markup("Disk Free") is False
        assert contains_markup(None) is False
        assert contains_markup("") is False


class TestAngleBracketGuarantee:
    """The safety property: whenever markup was stripped, no tag token remains."""

    def test_stripped_output_never_emits_an_angle_bracket(self):
        payloads = [
            PRTG_LOG_MESSAGE,
            "<div>a</div>",
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<table><tr><td>c</td></tr></table>",
            "<p>&lt;script&gt;</p>",
            "<p>&#60;b&#62;</p>",
            "<a href='j#\\' >x</a>",
        ]
        for payload in payloads:
            result = normalize_provider_text(payload)
            assert result.had_markup is True, payload
            assert "<" not in (result.text or ""), payload
            assert ">" not in (result.text or ""), payload

    def test_null_bytes_and_zero_width_are_removed(self):
        result = normalize_provider_text("<div>a\u200bb\ufeffc</div>")
        assert result.text == "abc"

    def test_nbsp_becomes_a_normal_space(self):
        result = normalize_provider_text("<div>a\u00a0b</div>")
        assert result.text == "a b"


# ---------------------------------------------------------------------------
# The API contract: alert.message is plain text, description stays raw
# ---------------------------------------------------------------------------

def _orm_alert(**overrides):
    """Stand-in for an Alert ORM row (AlertOut uses from_attributes=True)."""
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), site_id=None, device_id=None,
        sensor_id=None, interface_id=None, monitoring_source_id=None,
        title="IMAP", description=PRTG_LOG_MESSAGE, severity="warning",
        status="open", metric_type=None, category=None, vendor=None,
        region=None, source="prtg", triggered_value=None, threshold_value=None,
        triggered_at=now, last_occurred_at=now, occurrence_count=1,
        acknowledged_at=None, acknowledged_by=None, resolved_at=None,
        resolved_by=None, auto_resolved=False, correlation_id=None,
        incident_id=None, parent_alert_id=None, is_root_cause_candidate=False,
        is_suppressed=False, created_at=now, updated_at=now,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestAlertOutMessageContract:

    def test_message_is_plain_text_but_description_stays_raw(self):
        out = AlertOut.model_validate(_orm_alert())
        assert out.message == "Requested by PRTG System Administrator"
        # Raw payload is preserved for "Details -> Raw Event".
        assert out.description == PRTG_LOG_MESSAGE
        assert "<div" in out.description

    def test_markup_flag_is_set_for_provider_html(self):
        out = AlertOut.model_validate(_orm_alert())
        assert out.description_contains_markup is True

    def test_clean_description_yields_identical_message_and_no_flag(self):
        out = AlertOut.model_validate(_orm_alert(description="Sensor down"))
        assert out.message == "Sensor down"
        assert out.description_contains_markup is False

    def test_null_description_yields_null_message(self):
        out = AlertOut.model_validate(_orm_alert(description=None))
        assert out.message is None
        assert out.description_contains_markup is False

    def test_explicit_message_is_not_overwritten(self):
        out = AlertOut.model_validate(
            _orm_alert(message="Operator note", description=PRTG_LOG_MESSAGE)
        )
        assert out.message == "Operator note"
        # The flag still reports what the raw field contains.
        assert out.description_contains_markup is True

    def test_no_alertout_instance_can_carry_markup_in_message(self):
        """Property: for any description, `message` never contains a tag."""
        for description in (
            PRTG_LOG_MESSAGE,
            "<div>a</div><div>b</div>",
            "<script>alert(1)</script>safe",
            "<table><tr><td>cell</td></tr></table>",
            "plain",
            "",
        ):
            out = AlertOut.model_validate(_orm_alert(description=description))
            assert "<" not in (out.message or ""), description
