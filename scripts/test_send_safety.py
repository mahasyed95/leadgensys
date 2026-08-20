#!/usr/bin/env python3
"""
Safety tests for the sender. Run with:

    .venv/bin/python scripts/test_send_safety.py

These cover the paths where a bug sends mail it shouldn't. No credentials needed —
Gmail and Sheets are stubbed. They do not prove the live API calls work; only a
real --dry-run against your account does that.
"""

import os
import sys
import unittest
import warnings
from unittest import mock

warnings.filterwarnings("ignore", category=FutureWarning)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import send_approved as sa  # noqa: E402
import sheets  # noqa: E402


class TestComplianceFooter(unittest.TestCase):
    def test_adds_optout_when_absent(self):
        out = sa.compliance_footer("Hey Sarah, quick thought on your welcome flow.", "maha@x.com")
        self.assertIn("unsubscribe", out.lower())

    def test_does_not_duplicate_existing_optout(self):
        body = "Hey.\n\nReply unsubscribe to opt out."
        self.assertEqual(sa.compliance_footer(body, "maha@x.com").lower().count("unsubscribe"), 1)

    def test_identifies_sender(self):
        with mock.patch.object(sa, "SENDER_NAME", "Maha"):
            self.assertIn("Maha", sa.compliance_footer("Hi.", "maha@x.com"))

    # --- the postal address must survive any body ----------------------------
    # The footer used to return the body untouched whenever it contained the substring
    # "unsubscribe" or "opt out" — meaning to avoid a doubled opt-out line, but also
    # stripping the sender name, company and postal address. For an email-marketing
    # agency that misfire was close to guaranteed: "your unsubscribe rate is climbing"
    # is ordinary copy here, so those drafts would have shipped with no address at all.
    # CLAUDE.md Section 13: sending commercial email without one is illegal in the US.

    def _addr(self):
        return mock.patch.object(sa, "SENDER_ADDRESS", "1 High St, Cardiff CF11 1AA")

    def test_address_survives_a_body_that_mentions_unsubscribe(self):
        with self._addr(), mock.patch.object(sa, "SENDER_NAME", "Maha"):
            out = sa.compliance_footer("Your unsubscribe rate is climbing.", "m@x.com")
        self.assertIn("1 High St, Cardiff CF11 1AA", out)
        self.assertIn("Maha", out)

    def test_address_survives_a_body_mentioning_opt_out(self):
        with self._addr():
            out = sa.compliance_footer("Their opt-out flow is broken.", "m@x.com")
        self.assertIn("CF11 1AA", out)

    def test_address_present_even_when_optout_instruction_already_there(self):
        body = 'Quick note.\n\nReply "unsubscribe" and I will take you off this list.'
        with self._addr():
            out = sa.compliance_footer(body, "m@x.com")
        self.assertEqual(out.lower().count("unsubscribe"), 1)
        self.assertIn("CF11 1AA", out)

    def test_no_body_escapes_identification(self):
        with self._addr(), mock.patch.object(sa, "SENDER_NAME", "Maha"):
            for body in ["unsubscribe", "opt out", "opt-out rates", "plain text"]:
                out = sa.compliance_footer(body, "m@x.com")
                self.assertIn("Maha", out, body)
                self.assertIn("CF11 1AA", out, body)


class TestSenderAddressValidity(unittest.TestCase):
    """The guard checked the address was non-empty, not that it was deliverable.

    "30 Riverhead Close, Wales" is non-empty and passes any truthiness test, but a
    letter cannot arrive at it — no town, no postcode. Mail sent under it is exactly as
    non-compliant as mail sent with no address at all, so presence was never the test.
    """

    def test_empty_is_rejected(self):
        ok, why = sa.address_looks_deliverable("")
        self.assertFalse(ok)
        self.assertIn("not set", why)

    def test_street_and_country_with_no_postcode_is_rejected(self):
        ok, why = sa.address_looks_deliverable("30 Riverhead Close, Wales")
        self.assertFalse(ok)
        self.assertIn("postcode", why)

    def test_country_alone_is_rejected(self):
        self.assertFalse(sa.address_looks_deliverable("Wales")[0])

    def test_real_addresses_pass(self):
        for a in ["30 Riverhead Close, Cardiff CF11 1AA, UK",
                  "548 Market St, San Francisco, CA 94104",
                  "12 Main St #4, Toronto, ON M5V 2T6",
                  "5 Smith St, Sydney NSW 2000"]:
            self.assertTrue(sa.address_looks_deliverable(a)[0], a)

    def test_explicit_override_is_honoured(self):
        with mock.patch.object(sa.config, "get", return_value="1"):
            self.assertTrue(sa.address_looks_deliverable("Somewhere odd")[0])

    def test_run_once_refuses_an_undeliverable_address(self):
        rows = [row(approve="Approve Draft 1")]
        args = type("Args", (), {"dry_run": False, "limit": 0, "no_pace": True, "watch": 0})()
        with mock.patch.object(sa, "SENDER_ADDRESS", "30 Riverhead Close, Wales"), \
             mock.patch.object(sa.sheets, "read", return_value=rows), \
             mock.patch.object(sa.sheets, "suppressed_emails", return_value=(set(), set())), \
             mock.patch.object(sa, "local_suppression", return_value=(set(), set())), \
             mock.patch.object(sa, "load_sent_keys", return_value=set()), \
             mock.patch.object(sa.google_auth, "gmail") as gmail:
            with self.assertRaises(SystemExit) as cm:
                sa.run_once(args)
        self.assertIn("postcode", str(cm.exception))
        gmail.assert_not_called()


class TestReplyDetection(unittest.TestCase):
    def test_reply_found_stops_send(self):
        gmail = mock.MagicMock()
        gmail.users().messages().list().execute.return_value = {"messages": [{"id": "1"}]}
        self.assertTrue(sa.has_replied(gmail, "sarah@olivea.com"))

    def test_no_reply_allows_send(self):
        gmail = mock.MagicMock()
        gmail.users().messages().list().execute.return_value = {}
        self.assertFalse(sa.has_replied(gmail, "sarah@olivea.com"))

    def test_api_error_fails_closed(self):
        # If we cannot confirm the absence of a reply, we must NOT send.
        gmail = mock.MagicMock()
        gmail.users().messages().list().execute.side_effect = RuntimeError("network down")
        self.assertTrue(sa.has_replied(gmail, "sarah@olivea.com"))


def row(approve="", status="DRAFT", **over):
    """An Outbox row with three usable variants, unless a test blanks one out."""
    r = {"row_key": "a@b.com", "email": "a@b.com", "first_name": "Sam",
         "company": "Brand", "icp": "ecom", "signal": "welcome flow buries the CTA",
         "approve": approve, "status": status, "_row": 2}
    for n in (1, 2, 3):
        r["subject_%d" % n] = "subject %d" % n
        r["body_%d" % n] = "body %d" % n
    r.update(over)
    return r


def parse(msg):
    """Decode what build_message produced back into an email object. The body is
    base64 inside the MIME part, so it has to be decoded rather than string-matched."""
    import base64
    import email
    return email.message_from_bytes(base64.urlsafe_b64decode(msg["raw"]))


class TestMessageBuild(unittest.TestCase):
    def test_subject_and_body_come_from_the_chosen_variant(self):
        parsed = parse(sa.build_message(row(), "the winner", "chosen copy", "maha@x.com"))
        self.assertEqual(parsed["Subject"], "the winner")
        self.assertIn("chosen copy", parsed.get_payload(decode=True).decode())

    def test_recipient_is_the_row_email(self):
        parsed = parse(sa.build_message(row(email="sam@brand.com"), "s", "b", "maha@x.com"))
        self.assertEqual(parsed["To"], "sam@brand.com")

    def test_compliance_footer_is_applied_to_the_chosen_body(self):
        parsed = parse(sa.build_message(row(), "s", "no optout here", "maha@x.com"))
        self.assertIn("unsubscribe", parsed.get_payload(decode=True).decode().lower())

    def test_no_threading_headers(self):
        # One cold message per lead — there is no thread to reply into.
        parsed = parse(sa.build_message(row(), "hi", "x", "maha@x.com"))
        self.assertIsNone(parsed["In-Reply-To"])
        self.assertIsNone(parsed["References"])


class TestVariantSelection(unittest.TestCase):
    """approve is the entire safety gate, so it must fail closed on anything odd."""

    def test_each_label_selects_its_own_copy(self):
        for n in (1, 2, 3):
            variant, subject, body = sa.chosen_message(row(approve="Approve Draft %d" % n))
            self.assertEqual((variant, subject, body), (n, "subject %d" % n, "body %d" % n))

    def test_bare_numbers_still_work(self):
        # Rows pushed before the labelled dropdown existed carry these; stranding an
        # approval that was already given would be worse than accepting it.
        for n in (1, 2, 3):
            self.assertEqual(sa.chosen_message(row(approve=str(n)))[0], n)

    def test_blank_approve_sends_nothing(self):
        self.assertIsNone(sa.chosen_message(row(approve="")))

    def test_reject_and_redraft_never_send(self):
        for label in ("Reject", "Redraft", "reject", "  Redraft  "):
            self.assertIsNone(sa.chosen_message(row(approve=label)), label)

    def test_label_is_case_and_space_tolerant(self):
        self.assertEqual(sa.chosen_message(row(approve="  approve draft 2  "))[0], 2)

    def test_whitespace_and_float_forms_are_understood(self):
        # Sheets can hand back a typed 1 as "1.0"; a human can leave a space.
        self.assertEqual(sa.chosen_message(row(approve=" 2 "))[0], 2)
        self.assertEqual(sa.chosen_message(row(approve="3.0"))[0], 3)

    def test_out_of_range_and_junk_send_nothing(self):
        for bad in ("0", "4", "-1", "yes", "APPROVED", "1,2", "#REF!", "y", "all",
                    "Approve Draft 4", "Approve", "Draft 1", "Approve Draft"):
            self.assertIsNone(sa.chosen_message(row(approve=bad)), bad)

    def test_approving_an_empty_variant_sends_nothing(self):
        # Picking draft 3 when draft 3 was never written must not mail a blank.
        self.assertIsNone(sa.chosen_message(row(approve="Approve Draft 3",
                                                subject_3="", body_3="")))
        self.assertIsNone(sa.chosen_message(row(approve="Approve Draft 1", body_1="   ")))


class TestApproveAction(unittest.TestCase):
    def test_actions_are_classified(self):
        self.assertEqual(sheets.approve_action(row(approve="Approve Draft 2")), "approve")
        self.assertEqual(sheets.approve_action(row(approve="Reject")), "reject")
        self.assertEqual(sheets.approve_action(row(approve="Redraft")), "redraft")
        self.assertIsNone(sheets.approve_action(row(approve="")))
        self.assertIsNone(sheets.approve_action(row(approve="maybe later")))

    def test_rejected_rows_are_collected_but_not_eligible(self):
        rows = [row(approve="Reject"), row(approve="Approve Draft 1")]
        self.assertEqual(len(sa.rejected_rows(rows)), 1)
        self.assertEqual(len(sa.eligible_rows(rows)), 1)
        self.assertEqual(sa.rejected_rows(rows)[0]["approve"], "Reject")

    def test_already_cancelled_rejects_are_not_reprocessed(self):
        self.assertEqual(sa.rejected_rows([row(approve="Reject", status="CANCELLED")]), [])

    def test_redraft_is_neither_sent_nor_rejected(self):
        rows = [row(approve="Redraft")]
        self.assertEqual(sa.eligible_rows(rows), [])
        self.assertEqual(sa.rejected_rows(rows), [])


class TestGating(unittest.TestCase):
    """The filter that decides which rows are eligible at all."""

    def test_only_an_approved_variant_is_eligible(self):
        rows = [row(approve=""), row(approve="Approve Draft 2")]
        self.assertEqual(len(sa.eligible_rows(rows)), 1)

    def test_terminal_statuses_never_resend(self):
        for status in ("SENT", "CANCELLED", "FAILED", "HOLD"):
            self.assertEqual(sa.eligible_rows([row(approve="Approve Draft 1", status=status)]), [],
                             "%s row was eligible" % status)

    def test_blank_status_still_sends_when_approved(self):
        # A hand-added row may have no status typed in it at all.
        self.assertEqual(len(sa.eligible_rows([row(approve="Approve Draft 1", status="")])), 1)

    def test_status_is_case_and_space_tolerant(self):
        self.assertEqual(sa.eligible_rows([row(approve="Approve Draft 1", status=" sent ")]), [])

    def test_blank_variant_is_reported_not_silently_dropped(self):
        rows = [row(approve="Approve Draft 2", subject_2="", body_2="")]
        self.assertEqual(sa.eligible_rows(rows), [])
        self.assertEqual([(r["email"], v) for r, v in sa.approved_but_blank(rows)],
                         [("a@b.com", 2)])

    def test_unapproved_rows_are_not_reported_as_blank(self):
        self.assertEqual(sa.approved_but_blank([row(approve="")]), [])


class TestConditionalFormatFormulas(unittest.TestCase):
    """Row-shift immunity. push_to_sheet appends with INSERT_ROWS, and Sheets rewrites
    relative refs in conditional-format formulas when rows are inserted — which had
    every row being coloured by the state of the row below it."""

    def test_reference_survives_row_insertion(self):
        ref = sheets._this_row(12)  # column M
        self.assertEqual(ref, "INDEX($M:$M,ROW())")
        # No bare row number anywhere: that is the thing Sheets rewrites.
        self.assertNotRegex(ref, r"\$[A-Z]+\d")

    def test_armed_rule_reads_approve_and_status(self):
        cols = sheets.TABS[sheets.OUTBOX]
        approve = sheets._this_row(cols.index("approve"))
        status = sheets._this_row(cols.index("status"))
        self.assertNotEqual(approve, status)
        for ref in (approve, status):
            self.assertNotRegex(ref, r"\$[A-Z]+\d")

    def test_clear_requests_are_back_to_front(self):
        # Each delete reindexes the rules after it, so they must be removed in reverse.
        meta = {"sheets": [{"properties": {"sheetId": 7},
                            "conditionalFormats": [{}, {}, {}]}]}
        with mock.patch.object(sheets, "_run", return_value=meta):
            reqs = sheets._clear_conditional_formats("sid", 7, meta)
        idx = [r["deleteConditionalFormatRule"]["index"] for r in reqs]
        self.assertEqual(idx, [2, 1, 0])

    def test_clear_is_empty_for_a_clean_tab(self):
        meta = {"sheets": [{"properties": {"sheetId": 7}, "conditionalFormats": []}]}
        with mock.patch.object(sheets, "_run", return_value=meta):
            self.assertEqual(sheets._clear_conditional_formats("sid", 7, meta), [])


class TestAppendPreservesValidation(unittest.TestCase):
    """Appending leads must not strip the approve/li_status dropdowns.

    INSERT_ROWS inserted brand-new grid rows, which carry no data validation, and
    pushed the validated range down below them — so every freshly added lead landed
    with a free-text approve cell instead of the dropdown."""

    def test_append_does_not_insert_rows(self):
        api = mock.MagicMock()
        with mock.patch.object(sheets, "_api", return_value=api), \
             mock.patch.object(sheets, "_run", side_effect=lambda r, attempts=4: r), \
             mock.patch.object(sheets, "_existing_keys", return_value=set()), \
             mock.patch.object(sheets, "actual_headers", return_value=["email", "status"]):
            sheets.append(sheets.OUTBOX, [{"email": "a@b.com", "status": "DRAFT"}], "sid")
        kwargs = api.values().append.call_args.kwargs
        self.assertNotIn("insertDataOption", kwargs)

    def test_append_orders_values_by_real_header(self):
        api = mock.MagicMock()
        with mock.patch.object(sheets, "_api", return_value=api), \
             mock.patch.object(sheets, "_run", side_effect=lambda r, attempts=4: r), \
             mock.patch.object(sheets, "_existing_keys", return_value=set()), \
             mock.patch.object(sheets, "actual_headers", return_value=["status", "email"]):
            sheets.append(sheets.OUTBOX, [{"email": "a@b.com", "status": "DRAFT"}], "sid")
        self.assertEqual(api.values().append.call_args.kwargs["body"]["values"],
                         [["DRAFT", "a@b.com"]])

    def test_append_is_not_retried_blindly(self):
        """append is the one non-idempotent call in sheets.py.

        _run retries transient failures, so a timeout raised *after* the server had
        already appended would replay the whole write and give one lead two Outbox
        rows. Two identical rows both look approvable, so the lead gets mailed twice.
        append must therefore drive its own retry, calling _run with attempts=1.
        """
        api = mock.MagicMock()
        seen = {}

        def fake_run(req, attempts=4):
            seen["attempts"] = attempts
            return req

        with mock.patch.object(sheets, "_api", return_value=api), \
             mock.patch.object(sheets, "_run", side_effect=fake_run), \
             mock.patch.object(sheets, "_existing_keys", return_value=set()), \
             mock.patch.object(sheets, "actual_headers", return_value=["row_key"]):
            sheets.append(sheets.OUTBOX, [{"row_key": "a@b.com"}], "sid")
        self.assertEqual(seen["attempts"], 1)

    def test_append_rechecks_and_skips_rows_that_already_landed(self):
        """The retry must re-read first, so a half-completed attempt is not duplicated."""
        api = mock.MagicMock()
        calls = {"n": 0}

        def flaky(req, attempts=4):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("dropped after the server committed")
            return req

        # Second attempt sees the row already present, exactly as it would be in the
        # sheet after the first attempt actually landed.
        with mock.patch.object(sheets, "_api", return_value=api), \
             mock.patch.object(sheets, "_run", side_effect=flaky), \
             mock.patch.object(sheets, "_existing_keys",
                               side_effect=[set(), {"a@b.com"}]), \
             mock.patch.object(sheets, "actual_headers", return_value=["row_key"]), \
             mock.patch("time.sleep"):
            written = sheets.append(sheets.OUTBOX, [{"row_key": "a@b.com"}], "sid")
        self.assertEqual(written, 0, "row already in the sheet must not be appended again")

    def test_append_skips_rows_already_present(self):
        api = mock.MagicMock()
        with mock.patch.object(sheets, "_api", return_value=api), \
             mock.patch.object(sheets, "_run", side_effect=lambda r, attempts=4: r), \
             mock.patch.object(sheets, "_existing_keys", return_value={"dup@b.com"}), \
             mock.patch.object(sheets, "actual_headers", return_value=["row_key"]):
            n = sheets.append(sheets.OUTBOX,
                              [{"row_key": "dup@b.com"}, {"row_key": "new@b.com"}], "sid")
        self.assertEqual(n, 1)
        self.assertEqual(api.values().append.call_args.kwargs["body"]["values"],
                         [["new@b.com"]])

    def test_nothing_to_append_touches_no_api(self):
        with mock.patch.object(sheets, "_api", side_effect=AssertionError("should not run")):
            self.assertEqual(sheets.append(sheets.OUTBOX, [], "sid"), 0)


class TestLeadStageTracking(unittest.TestCase):
    """The Leads tab is the CRM record; it must not stay on 'pending review' after
    the message has actually gone out."""

    def test_status_is_written_to_the_matching_lead(self):
        rows = [{"email": "other@x.com", "_row": 2}, {"email": "Sam@Brand.com", "_row": 3}]
        with mock.patch.object(sheets, "read", return_value=rows), \
             mock.patch.object(sheets, "update_cells") as upd:
            self.assertTrue(sheets.set_lead_status("sam@brand.com", "Sent"))
        upd.assert_called_once_with(sheets.LEADS, [(3, "status", "Sent")], None)

    def test_unknown_lead_is_not_an_error(self):
        with mock.patch.object(sheets, "read", return_value=[]), \
             mock.patch.object(sheets, "update_cells") as upd:
            self.assertFalse(sheets.set_lead_status("nobody@x.com", "Sent"))
        upd.assert_not_called()

    def test_blank_email_touches_nothing(self):
        with mock.patch.object(sheets, "update_cells") as upd:
            self.assertFalse(sheets.set_lead_status("", "Sent"))
        upd.assert_not_called()

    def test_crm_failure_never_breaks_the_send_loop(self):
        # Runs after delivery — raising here would abort the rest of the batch.
        with mock.patch.object(sa.sheets, "set_lead_status",
                               side_effect=RuntimeError("api down")):
            sa.set_lead_stage("a@b.com", "Sent")


class TestPostalAddressBlock(unittest.TestCase):
    """CAN-SPAM requires a physical postal address. Without one the sender must refuse
    outright — a printed warning in an unattended run is read by nobody, and by then
    the mail has already gone."""

    def args(self, **kw):
        base = {"dry_run": False, "limit": 0, "no_pace": True, "watch": 0}
        base.update(kw)
        return type("Args", (), base)()

    def test_refuses_to_send_with_no_address(self):
        rows = [row(approve="Approve Draft 1")]
        with mock.patch.object(sa, "SENDER_ADDRESS", ""), \
             mock.patch.object(sa.sheets, "read", return_value=rows), \
             mock.patch.object(sa.sheets, "suppressed_emails", return_value=(set(), set())), \
             mock.patch.object(sa, "local_suppression", return_value=(set(), set())), \
             mock.patch.object(sa, "load_sent_keys", return_value=set()), \
             mock.patch.object(sa.google_auth, "gmail") as gmail:
            with self.assertRaises(SystemExit) as cm:
                sa.run_once(self.args())
        self.assertIn("SENDER_ADDRESS", str(cm.exception))
        gmail.assert_not_called()   # never even authenticated

    def test_dry_run_still_works_without_an_address(self):
        # Review must not be blocked by a compliance gap that only affects sending.
        rows = [row(approve="Approve Draft 1")]
        with mock.patch.object(sa, "SENDER_ADDRESS", ""), \
             mock.patch.object(sa.sheets, "read", return_value=rows), \
             mock.patch.object(sa.sheets, "suppressed_emails", return_value=(set(), set())), \
             mock.patch.object(sa, "local_suppression", return_value=(set(), set())), \
             mock.patch.object(sa, "load_sent_keys", return_value=set()):
            sa.run_once(self.args(dry_run=True))   # must not raise


class TestSuppression(unittest.TestCase):
    def test_local_csv_suppression_is_read(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "suppression.csv")
        with open(path, "w") as f:
            f.write("email,domain,company,reason,date_added\n")
            f.write("rob@nova.com,nova.com,Nova,unsubscribed,2026-01-01\n")
        with mock.patch.object(sa, "LOCAL_SUPPRESSION", path):
            emails, domains = sa.local_suppression()
        self.assertIn("rob@nova.com", emails)
        self.assertIn("nova.com", domains)

    def test_missing_file_is_not_an_error(self):
        with mock.patch.object(sa, "LOCAL_SUPPRESSION", "/nonexistent/path.csv"):
            self.assertEqual(sa.local_suppression(), (set(), set()))


class TestLedgerClosesTheLoop(unittest.TestCase):
    """A send must land in master-list.csv, or the next Apollo pull re-contacts them."""

    def setUp(self):
        import tempfile
        import record_sent
        self.record_sent = record_sent
        self.tmp = tempfile.mkdtemp()
        self.master = os.path.join(self.tmp, "master-list.csv")
        with open(self.master, "w") as f:
            f.write("email,domain,company,first_name,icp,batch_id,date_first_contacted,status\n")
        self.patch = mock.patch.object(record_sent, "MASTER", self.master)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def rows(self):
        import csv
        with open(self.master) as f:
            return list(csv.DictReader(f))

    def test_send_is_recorded(self):
        self.assertTrue(self.record_sent.record_send("a@brand.com", company="Brand", icp="ecom"))
        self.assertEqual(self.rows()[0]["email"], "a@brand.com")

    def test_recording_is_idempotent(self):
        self.record_sent.record_send("a@brand.com")
        self.record_sent.record_send("a@brand.com")
        self.assertEqual(len(self.rows()), 1)

    def test_domain_is_normalized_so_company_is_blocked(self):
        # Recorded from a subdomain address, but must block the whole company.
        self.record_sent.record_send("a@shop.brand.com")
        self.assertEqual(self.rows()[0]["domain"], "brand.com")

    def test_blank_email_is_ignored(self):
        self.assertFalse(self.record_sent.record_send(""))
        self.assertEqual(self.rows(), [])


class TestSentLedgerBackstop(unittest.TestCase):
    """If the Sheet write fails after delivery, the row stays APPROVED. This local
    ledger is what stops the next cron run re-sending to a real person."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, ".sent-row-keys")
        self.patch = mock.patch.object(sa, "SENT_KEYS", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_empty_when_no_file(self):
        self.assertEqual(sa.load_sent_keys(), set())

    def test_marked_key_is_remembered(self):
        sa.mark_sent_key({"row_key": "a@b.com|1"})
        self.assertIn("a@b.com|1", sa.load_sent_keys())

    def test_survives_repeated_marks(self):
        sa.mark_sent_key({"row_key": "a@b.com|1"})
        sa.mark_sent_key({"row_key": "a@b.com|2"})
        self.assertEqual(sa.load_sent_keys(), {"a@b.com|1", "a@b.com|2"})

    def test_row_without_row_key_falls_back_to_email(self):
        """load_sent_keys() discards blank lines, so a row_key-less row used to record
        nothing at all — leaving it with no protection against a re-send after a failed
        sheet write."""
        sa.mark_sent_key({"row_key": "", "email": "NoKey@B.com"})
        self.assertIn("nokey@b.com", sa.load_sent_keys())

    def test_row_with_no_identity_records_nothing_rather_than_a_blank_line(self):
        sa.mark_sent_key({"row_key": "", "email": ""})
        self.assertEqual(sa.load_sent_keys(), set())

    def test_sent_key_matches_between_write_and_lookup(self):
        row = {"row_key": "", "email": "Someone@Example.com"}
        sa.mark_sent_key(row)
        self.assertIn(sa.sent_key(row), sa.load_sent_keys())

    def test_write_status_failure_does_not_raise(self):
        # Delivery already happened; a bookkeeping failure must not abort the batch.
        with mock.patch.object(sa.sheets, "resolve_row", side_effect=RuntimeError("api down")):
            self.assertFalse(sa.write_status({"row_key": "a@b.com|1", "_row": 5},
                                             [("status", "SENT")]))


class TestPushToSheetSafety(unittest.TestCase):
    """push_to_sheet is the last gate before a lead becomes contactable.

    These drive the real main() with Sheets mocked out, so they test what actually
    gets appended rather than a restatement of it.
    """

    def setUp(self):
        import json
        import tempfile
        import push_to_sheet
        self.pts = push_to_sheet
        self.json = json
        self.tmp = tempfile.mkdtemp()

    def _draft(self, *leads):
        path = os.path.join(self.tmp, "d.json")
        with open(path, "w") as f:
            self.json.dump(list(leads), f)
        return path

    def _lead(self, email, **kw):
        d = {"email": email, "first_name": "A", "company": "C", "icp": "ecom",
             "signal": "a real observed signal",
             "variants": [{"subject": "s%d" % n, "body": "b%d" % n} for n in (1, 2, 3)]}
        d.update(kw)
        return d

    def _push(self, path, existing_outbox=()):
        """Run main(), return the Outbox rows it tried to append."""
        appended = {}

        def fake_append(tab, dicts, *a, **kw):
            appended.setdefault(tab, []).extend(dicts)
            return len(dicts)

        def fake_read(tab, *a, **kw):
            return list(existing_outbox) if tab == sheets.OUTBOX else []

        with mock.patch.object(self.pts.sheets, "ensure_tabs"), \
             mock.patch.object(self.pts.sheets, "format_tabs"), \
             mock.patch.object(self.pts.sheets, "sheet_id", return_value="sid"), \
             mock.patch.object(self.pts.sheets, "read", side_effect=fake_read), \
             mock.patch.object(self.pts.sheets, "existing_row_keys",
                               return_value={(r.get("row_key") or "") for r in existing_outbox}), \
             mock.patch.object(self.pts.sheets, "append", side_effect=fake_append), \
             mock.patch.object(sys, "argv", ["push_to_sheet.py", path]):
            self.pts.main()
        return appended.get(sheets.OUTBOX, [])

    def test_li_status_is_never_written(self):
        """CLAUDE.md Sections 12 and 13 reserve li_status for Maha. The code used to
        seed it with 'To send' directly beneath a comment claiming nothing wrote it."""
        rows = self._push(self._draft(self._lead(
            "a@acme.com", linkedin_url="https://li/x",
            linkedin={"note": "n", "dm": "d"})))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("li_status", rows[0])

    def test_approve_is_always_blank(self):
        rows = self._push(self._draft(self._lead("a@acme.com")))
        self.assertEqual(rows[0]["approve"], "")

    def test_status_is_draft(self):
        rows = self._push(self._draft(self._lead("a@acme.com")))
        self.assertEqual(rows[0]["status"], sheets.DRAFT)

    def test_second_contact_at_same_company_is_dropped(self):
        """CLAUDE.md Section 8: one person per company at a time. Matching on email
        alone let alice@acme.com and bob@acme.com both into the Outbox, and mailing
        two people at one company reads as spam and burns the sending domain."""
        rows = self._push(self._draft(self._lead("bob@acme.com")),
                          existing_outbox=[{"row_key": "alice@acme.com",
                                            "email": "alice@acme.com"}])
        self.assertEqual(rows, [], "bob@acme.com duplicates alice's company")

    def test_two_contacts_at_one_company_within_a_batch(self):
        rows = self._push(self._draft(self._lead("a@acme.com"), self._lead("b@acme.com")))
        self.assertEqual(len(rows), 1)

    def test_different_companies_both_pass(self):
        rows = self._push(self._draft(self._lead("a@acme.com"), self._lead("b@other.com")))
        self.assertEqual(len(rows), 2)

    def test_freemail_contacts_are_not_treated_as_one_company(self):
        rows = self._push(self._draft(self._lead("a@gmail.com"), self._lead("b@gmail.com")))
        self.assertEqual(len(rows), 2, "gmail.com is not a company")

    def test_lead_domain_ignores_freemail(self):
        self.assertEqual(self.pts.lead_domain("x@gmail.com"), "")
        self.assertEqual(self.pts.lead_domain("x@shop.acme.co.uk"), "acme.co.uk")
        self.assertEqual(self.pts.lead_domain(""), "")

    def test_revenue_that_is_not_a_number_does_not_crash(self):
        self.assertIn("12M", self.pts.icp_evidence({"icp": "ecom", "revenue": "12M"}))
        self.assertIn("$12M", self.pts.icp_evidence({"icp": "ecom", "revenue": 12_000_000}))


class TestSendRetry(unittest.TestCase):
    def test_transient_error_is_retried(self):
        gmail = mock.MagicMock()
        gmail.users().messages().send().execute.side_effect = [
            TimeoutError("boom"), {"id": "1", "threadId": "t"},
        ]
        with mock.patch.object(sa.time, "sleep"):
            self.assertEqual(sa.send_with_retry(gmail, {"raw": "x"})["id"], "1")

    def test_permanent_error_is_not_retried(self):
        gmail = mock.MagicMock()
        gmail.users().messages().send().execute.side_effect = ValueError("bad address")
        with mock.patch.object(sa.time, "sleep"):
            with self.assertRaises(ValueError):
                sa.send_with_retry(gmail, {"raw": "x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
