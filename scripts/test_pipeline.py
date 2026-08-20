#!/usr/bin/env python3
"""
Regression tests for the lead pipeline. No dependencies — run with:

    python3 scripts/test_pipeline.py

Dedup and ICP classification are the two things that must never silently break:
a dedup miss double-contacts a company and burns the sending domain; an ICP
misclassification sends an agency the ecom pitch, which namedrops client work.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import process_batch as pb  # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)

HEADER = ("First Name,Last Name,Title,Company,Email,Email Status,Website,"
          "Person Linkedin Url,# Employees,Industry,Technologies,Country,City")


def row(first="A", last="B", title="Founder", company="Acme", email="a@acme.com",
        status="Verified", website="https://acme.com", li="", emp="40",
        industry="Retail", tech="Shopify", country="United States", city="Austin"):
    return ",".join([first, last, title, company, email, status, website, li,
                     emp, industry, f'"{tech}"', country, city])


class TestColumnMatching(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(pb.pick({"Email": "a@b.com"}, "Email"), "a@b.com")

    def test_punctuation_and_spacing_drift(self):
        self.assertEqual(pb.pick({"# Employees": "40"}, "Employees"), "40")

    def test_never_bleeds_into_a_longer_column(self):
        # 'Email' must not resolve to 'Email Status', nor 'Company' to 'Company Phone'.
        self.assertEqual(pb.pick({"Email Status": "Verified"}, "Email"), "")
        self.assertEqual(pb.pick({"Company Phone": "555"}, "Company"), "")

    def test_falls_through_aliases(self):
        self.assertEqual(pb.pick({"Company Website": "x.com"}, "Website", "Company Website"), "x.com")


class TestDomain(unittest.TestCase):
    def test_subdomain_collapses_to_company(self):
        self.assertEqual(pb.domain_of("", "https://shop.brand.com"), "brand.com")
        self.assertEqual(pb.domain_of("", "https://brand.com"), "brand.com")

    def test_multipart_tld_not_over_collapsed(self):
        # The dangerous failure: collapsing to 'co.uk' would merge every UK company.
        self.assertEqual(pb.domain_of("", "https://www.brand.co.uk"), "brand.co.uk")
        self.assertEqual(pb.domain_of("", "https://shop.brand.co.uk"), "brand.co.uk")
        self.assertEqual(pb.domain_of("", "https://brand.com.au"), "brand.com.au")

    def test_two_uk_companies_stay_distinct(self):
        self.assertNotEqual(pb.domain_of("", "https://alpha.co.uk"),
                            pb.domain_of("", "https://beta.co.uk"))

    def test_freemail_yields_no_company_domain(self):
        self.assertEqual(pb.domain_of("someone@gmail.com", ""), "")

    def test_falls_back_to_email_domain(self):
        self.assertEqual(pb.domain_of("a@brand.com", ""), "brand.com")


class TestICP(unittest.TestCase):
    def test_agency_running_klaviyo_is_still_an_agency(self):
        # Agencies run Klaviyo/Shopify for clients. Tech stack must not override this,
        # or the agency gets the ecom pitch and its client-namedrop case studies.
        self.assertEqual(pb.classify_icp("Marketing & Advertising", "Klaviyo, Shopify", "Northgate", ""), "agency")

    def test_ecom_brand(self):
        self.assertEqual(pb.classify_icp("Health & Wellness", "Shopify, Klaviyo", "Olivea", ""), "ecom")

    def test_ecom_named_studio_is_not_an_agency(self):
        self.assertEqual(pb.classify_icp("Apparel", "Shopify", "Tidal Studio", ""), "ecom")

    def test_no_signal_yields_no_match(self):
        self.assertEqual(pb.classify_icp("Mining", "", "Rock Co", ""), "")


class TestRoleEmailGate(unittest.TestCase):
    """firecrawl_source.py emits company-level rows: role inbox, no named person.
    These must stay rejected unless the operator opts in per batch."""

    @staticmethod
    def lead(**kw):
        base = dict(email="hello@brand.com", email_status="scraped", country="United States",
                    domain="brand.com", seniority=0, title="", icp="ecom", employees=40,
                    first_name="")
        base.update(kw)
        return base

    def test_scraped_status_rejected_by_default(self):
        self.assertIsNotNone(pb.qualify(self.lead(), None, check_mx=False))

    def test_scraped_status_allowed_when_opted_in(self):
        self.assertIsNone(pb.qualify(self.lead(), None, allow_role_emails=True, check_mx=False))

    def test_optin_does_not_excuse_a_junior_named_contact(self):
        # A real person with a junior title is still wrong, opt-in or not.
        self.assertIsNotNone(pb.qualify(
            self.lead(title="Marketing Coordinator"), None, allow_role_emails=True, check_mx=False))

    def test_optin_does_not_excuse_a_bad_email_status(self):
        self.assertIsNotNone(pb.qualify(
            self.lead(email_status="guessed"), None, allow_role_emails=True, check_mx=False))


class TestStrictICP(unittest.TestCase):
    """Filters added after a live batch put three leads in front of a human that the
    code should have caught: a B2B SaaS company, a brand far outside the revenue band,
    and (apparently) a domain that could not receive mail."""

    @staticmethod
    def lead(**kw):
        base = dict(email="sam@brand.com", email_status="verified", country="United States",
                    domain="brand.com", seniority=100, title="Founder", icp="ecom",
                    employees=40, revenue=5_000_000, industry="apparel & fashion",
                    technologies="Shopify, Klaviyo", first_name="Sam")
        base.update(kw)
        return base

    def q(self, **kw):
        return pb.qualify(self.lead(**kw), None, check_mx=False)

    def test_a_good_ecom_lead_still_passes(self):
        self.assertIsNone(self.q())

    # --- B2B exclusion (Yuna Health: AI mental-health platform selling to employers) ---
    def test_b2b_saas_industry_is_rejected(self):
        reason = self.q(industry="information technology & services", technologies="")
        self.assertIn("B2B", reason)

    def test_b2b_industries_are_all_covered(self):
        for ind in ("computer software", "financial services", "staffing & recruiting",
                    "management consulting", "nonprofit organization management"):
            self.assertIsNotNone(self.q(industry=ind, technologies=""), ind)

    def test_real_store_with_a_vague_industry_survives(self):
        # A genuine Shopify store can carry a lazy industry label; the tech stack is
        # better evidence than the label, so it gets a veto.
        self.assertIsNone(self.q(industry="internet", technologies="Shopify, Recharge"))

    def test_b2b_exclusion_does_not_apply_to_agencies(self):
        # ICP #2 is deliberately a B2B audience.
        self.assertIsNone(pb.qualify(
            self.lead(icp="agency", industry="management consulting", employees=20,
                      revenue=0, technologies=""), None, check_mx=False))

    # --- Revenue band (ONNIT: 140 staff, nine figures) ---
    def test_revenue_above_band_is_rejected(self):
        self.assertIn("revenue", self.q(revenue=250_000_000))

    def test_revenue_below_band_is_rejected(self):
        self.assertIn("revenue", self.q(revenue=200_000))

    def test_unknown_revenue_is_not_judged(self):
        # 0 means Apollo had no figure — that must not silently drop a good lead.
        self.assertIsNone(self.q(revenue=0))

    def test_band_edges_are_inclusive(self):
        self.assertIsNone(self.q(revenue=1_000_000))
        self.assertIsNone(self.q(revenue=20_000_000))


class TestRevenueParsing(unittest.TestCase):
    def test_apollo_float_form(self):
        self.assertEqual(pb.parse_revenue("10000000.0"), 10_000_000)

    def test_suffixed_and_punctuated_forms(self):
        self.assertEqual(pb.parse_revenue("10M"), 10_000_000)
        self.assertEqual(pb.parse_revenue("$1,500,000"), 1_500_000)
        self.assertEqual(pb.parse_revenue("500k"), 500_000)
        self.assertEqual(pb.parse_revenue("1.2B"), 1_200_000_000)

    def test_unknown_is_zero_not_a_crash(self):
        for raw in ("", None, "n/a", "unknown", "--"):
            self.assertEqual(pb.parse_revenue(raw), 0, repr(raw))


class TestMXCheck(unittest.TestCase):
    """A domain with no MX cannot receive mail, so every send to it hard-bounces."""

    def setUp(self):
        pb._mx_cache.clear()

    def tearDown(self):
        pb._mx_cache.clear()

    HEADER = ";; ->>HEADER<<- opcode: QUERY, status: %s, id: 1\n"

    def _dig(self, stdout, returncode=0):
        return mock.Mock(return_value=mock.Mock(stdout=stdout, returncode=returncode))

    def _replies(self, *outs):
        """One canned dig response per call, in order (MX lookup, then A lookup)."""
        return mock.Mock(side_effect=[mock.Mock(stdout=o, returncode=0) for o in outs])

    def test_domain_with_mx_passes(self):
        out = self.HEADER % "NOERROR" + "brand.com.\t300\tIN\tMX\t10 aspmx.l.google.com.\n"
        with mock.patch("subprocess.run", self._dig(out)):
            self.assertTrue(pb.has_mx("brand.com"))

    def test_nonexistent_domain_is_rejected(self):
        with mock.patch("subprocess.run", self._dig(self.HEADER % "NXDOMAIN")):
            self.assertFalse(pb.has_mx("dead.com"))
        pb._mx_cache.clear()
        with mock.patch("subprocess.run", self._dig(self.HEADER % "NXDOMAIN")):
            self.assertIn("no MX", pb.qualify(
                TestStrictICP.lead(domain="dead.com"), None, check_mx=True))

    def test_no_mx_but_an_a_record_still_delivers(self):
        # RFC 5321 falls back to the A record, so absent MX is not proof of a dead
        # mailbox — condemning it would drop real leads.
        with mock.patch("subprocess.run", self._replies(self.HEADER % "NOERROR", "93.184.216.34\n")):
            self.assertTrue(pb.has_mx("brand.com"))

    def test_no_mx_and_no_a_record_is_rejected(self):
        with mock.patch("subprocess.run", self._replies(self.HEADER % "NOERROR", "")):
            self.assertFalse(pb.has_mx("brand.com"))

    def test_servfail_fails_open(self):
        # The bug this guards: `dig +short` prints nothing on SERVFAIL and still exits
        # 0, which read as "no MX" and wrongly condemned two live domains.
        with mock.patch("subprocess.run", self._dig(self.HEADER % "SERVFAIL")):
            self.assertTrue(pb.has_mx("masterofmalt.com"))

    def test_dns_exception_fails_open(self):
        with mock.patch("subprocess.run", side_effect=OSError("resolver down")):
            self.assertTrue(pb.has_mx("brand.com"))

    def test_nonzero_exit_fails_open(self):
        with mock.patch("subprocess.run", self._dig("", returncode=9)):
            self.assertTrue(pb.has_mx("brand.com"))

    def test_result_is_cached(self):
        out = self.HEADER % "NOERROR" + "brand.com.\t300\tIN\tMX\t10 mx.example.com.\n"
        dig = self._dig(out)
        with mock.patch("subprocess.run", dig):
            pb.has_mx("brand.com")
            pb.has_mx("brand.com")
        self.assertEqual(dig.call_count, 1)

    def test_blank_domain_is_false_without_a_lookup(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("should not run")):
            self.assertFalse(pb.has_mx(""))


class TestFirecrawlParsing(unittest.TestCase):
    """Parsing helpers decide lead quality, and run before any network call."""

    def setUp(self):
        import firecrawl_source
        self.fs = firecrawl_source

    def test_detects_live_tech_stack(self):
        html = ('<script src="https://cdn.shopify.com/s/f.js"></script>'
                '<script src="https://static.klaviyo.com/onsite/js/klaviyo.js"></script>')
        self.assertEqual(set(self.fs.detect_tech(html)), {"Shopify", "Klaviyo"})

    def test_no_false_positive_on_plain_page(self):
        self.assertEqual(self.fs.detect_tech("<html><body>hi</body></html>"), [])

    def test_named_address_beats_role_inbox(self):
        self.assertEqual(
            self.fs.best_email("hello@brand.com or sarah@brand.com", "brand.com"),
            "sarah@brand.com")

    def test_prefers_company_domain_over_third_party(self):
        self.assertEqual(
            self.fs.best_email("partner@other.com and info@brand.com", "brand.com"),
            "info@brand.com")

    def test_rejects_noreply_and_image_filenames(self):
        self.assertEqual(self.fs.best_email("noreply@brand.com", "brand.com"), "")
        self.assertEqual(self.fs.best_email("logo@2x.png hero.jpg", "brand.com"), "")

    def test_rejects_mhtml_frame_markers(self):
        # Chrome MHTML markers match the email regex exactly; one reached a lead CSV.
        junk = "frame-747003c19db207ca22d064bf2d65e6f7@mhtml.blink"
        self.assertFalse(self.fs.plausible_email(junk))
        self.assertEqual(self.fs.best_email(junk, "rhodeskin.com"), "")

    def test_rejects_hashes_and_invalid_tlds(self):
        for bad in ("a1b2c3d4e5f60718@brand.com", "x@brand.localhost", "x@brand.123"):
            self.assertFalse(self.fs.plausible_email(bad), bad)

    def test_still_accepts_real_addresses(self):
        for good in ("bonjour@orrisparis.com", "sarah@brand.co.uk", "hello@brand.com.au"):
            self.assertTrue(self.fs.plausible_email(good), good)

    def test_domain_matches_what_dedup_will_compare(self):
        # Must agree with process_batch.domain_of, or duplicates slip through.
        for url in ("https://www.brand.com/collections/all", "https://brand.com?x=1",
                    "https://brand.com/#top"):
            self.assertEqual(self.fs.registrable(url), "brand.com", url)
        self.assertEqual(self.fs.registrable("shop.brand.co.uk"), "brand.co.uk")

    def test_country_inferred_from_tld(self):
        self.assertEqual(self.fs.country_from_domain("brand.co.uk"), "United Kingdom")
        self.assertEqual(self.fs.country_from_domain("brand.com.au"), "Australia")
        self.assertEqual(self.fs.country_from_domain("brand.com"), "")


class TestSeniority(unittest.TestCase):
    def test_junior_titles_score_zero(self):
        for t in ["Marketing Coordinator", "Intern", "Marketing Assistant"]:
            self.assertEqual(pb.seniority_score(t), 0, t)

    def test_founder_outranks_manager(self):
        self.assertGreater(pb.seniority_score("Founder"), pb.seniority_score("Marketing Manager"))


class TestDiversityAxis(unittest.TestCase):
    """The 25% cap must not count 'marketing & advertising' against an agency batch.

    That industry is not a niche that crept in — for ICP #2 it *is* the ICP. Counting
    it would have rejected roughly three quarters of any agency batch of 20+ for the
    crime of being made of agencies. The 2026-08-17 batch (10/12 marketing &
    advertising) was under DIVERSIFY_MIN_BATCH so it only produced a spurious warning,
    but the same code would have trimmed a full-size batch.
    """

    def test_agency_diversifies_on_size_not_industry(self):
        self.assertEqual(pb.diversity_axis("agency"), "size band")

    def test_ecom_still_diversifies_on_industry(self):
        self.assertEqual(pb.diversity_axis("ecom"), "industry")

    def test_same_industry_agencies_get_distinct_keys(self):
        leads = [{"employees": n, "industry": "Marketing & Advertising"}
                 for n in (5, 17, 28, 60)]
        keys = {pb.diversity_key(l, "agency") for l in leads}
        self.assertEqual(len(keys), 4, "size bands should separate same-industry agencies")

    def test_missing_headcount_is_unknown_not_smallest_band(self):
        self.assertEqual(pb.size_band(0), "unknown")
        self.assertEqual(pb.size_band(None), "unknown")
        self.assertNotEqual(pb.size_band(0), pb.size_band(5))

    def test_size_band_edges(self):
        for n, want in ((1, "1-9"), (9, "1-9"), (10, "10-19"), (19, "10-19"),
                        (20, "20-49"), (49, "20-49"), (50, "50+")):
            self.assertEqual(pb.size_band(n), want, n)

    def test_ecom_key_is_normalised_industry(self):
        self.assertEqual(
            pb.diversity_key({"employees": 30, "industry": "Apparel & Fashion"}, "ecom"),
            "apparel & fashion")


class TestEndToEnd(unittest.TestCase):
    """Runs the real script against temp ledgers so the live ones are untouched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.master = os.path.join(self.tmp, "master-list.csv")
        self.suppression = os.path.join(self.tmp, "suppression.csv")
        with open(self.master, "w") as f:
            f.write("email,domain,company,first_name,icp,batch_id,date_first_contacted,status\n")
        with open(self.suppression, "w") as f:
            f.write("email,domain,company,reason,date_added\n")

    def run_batch(self, rows, extra_args=()):
        csv_path = os.path.join(self.tmp, "in.csv")
        with open(csv_path, "w") as f:
            f.write(HEADER + "\n" + "\n".join(rows) + "\n")
        env = dict(os.environ, OUTREACH_MASTER=self.master, OUTREACH_SUPPRESSION=self.suppression,
                   OUTREACH_OUTDIR=self.tmp)
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "process_batch.py"), csv_path, *extra_args],
            capture_output=True, text=True, env=env, cwd=ROOT,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_one_contact_per_company_keeps_most_senior(self):
        out = self.run_batch([
            row(first="Greg", title="Head of Growth", email="greg@tidal.com", website="https://tidal.com", company="Tidal"),
            row(first="Dan", title="VP Marketing", email="dan@tidal.com", website="https://tidal.com", company="Tidal"),
        ])
        self.assertIn("Qualified: 1", out)

    def test_subdomain_variant_is_same_company(self):
        out = self.run_batch([
            row(first="A", title="Founder", email="a@tidal.com", website="https://tidal.com", company="Tidal"),
            row(first="B", title="CMO", email="b@tidal.com", website="https://shop.tidal.com", company="Tidal"),
        ])
        self.assertIn("Qualified: 1", out)

    def test_suppressed_lead_is_blocked(self):
        with open(self.suppression, "a") as f:
            f.write("rob@nova.com,nova.com,Nova,unsubscribed,2026-01-01\n")
        out = self.run_batch([row(first="Rob", email="rob@nova.com", website="https://nova.com", company="Nova")])
        self.assertIn("Qualified: 0", out)

    def test_master_list_blocks_by_domain_only(self):
        # Different person, same company — must still be blocked.
        with open(self.master, "a") as f:
            f.write("old@nova.com,nova.com,Nova,Old,ecom,b1,2026-01-01,Sent\n")
        out = self.run_batch([row(first="New", email="new@nova.com", website="https://nova.com", company="Nova")])
        self.assertIn("Qualified: 0", out)

    def test_clean_lead_passes(self):
        out = self.run_batch([row(first="Sara", email="sara@fresh.com", website="https://fresh.com", company="Fresh")])
        self.assertIn("Qualified: 1", out)

    def test_out_of_scope_country_rejected(self):
        out = self.run_batch([row(email="x@jp.com", website="https://jp.com", country="Japan")])
        self.assertIn("Qualified: 0", out)

    def test_duplicate_across_separate_files_is_caught(self):
        """Apollo's free tier caps exports at 25 records, so one batch arrives as
        several files. The same company in two of them must not yield two sequences."""
        d = os.path.join(self.tmp, "multi")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "a.csv"), "w") as f:
            f.write(HEADER + "\n" + row(first="Sarah", title="Founder",
                    email="sarah@olivea.com", website="https://olivea.com", company="Olivea") + "\n")
        with open(os.path.join(d, "b.csv"), "w") as f:
            f.write(HEADER + "\n" + row(first="Dan", title="VP Marketing",
                    email="dan@olivea.com", website="https://shop.olivea.com", company="Olivea") + "\n")
        env = dict(os.environ, OUTREACH_MASTER=self.master,
                   OUTREACH_SUPPRESSION=self.suppression, OUTREACH_OUTDIR=self.tmp)
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "process_batch.py"), d],
            capture_output=True, text=True, env=env, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("merged 2 rows from 2 files", out.stdout)
        self.assertIn("Qualified: 1", out.stdout)

    def test_eu_country_accepted(self):
        out = self.run_batch([row(email="x@de.com", website="https://de.com", country="Germany")])
        self.assertIn("Qualified: 1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
