"""Unit tests for multi-domain sender parsing and Gmail query fragments."""

import unittest

from gmail_client import gmail_from_domain_clause, normalize_sender_domains


class NormalizeSenderDomainsTests(unittest.TestCase):
    def test_single_domain(self) -> None:
        self.assertEqual(normalize_sender_domains("partner.com"), ["partner.com"])

    def test_comma_separated(self) -> None:
        self.assertEqual(
            normalize_sender_domains("a.com, b.com;c.com"),
            ["a.com", "b.com", "c.com"],
        )

    def test_strips_at_and_dedupes(self) -> None:
        self.assertEqual(
            normalize_sender_domains("@foo.com, foo.com, FOO.com"),
            ["foo.com"],
        )

    def test_sequence(self) -> None:
        self.assertEqual(normalize_sender_domains(["x.com", "y.com"]), ["x.com", "y.com"])


class GmailFromDomainClauseTests(unittest.TestCase):
    def test_single(self) -> None:
        self.assertEqual(gmail_from_domain_clause(["a.com"]), "from:*@a.com")

    def test_multiple_or(self) -> None:
        self.assertEqual(
            gmail_from_domain_clause(["a.com", "b.com"]),
            "(from:*@a.com OR from:*@b.com)",
        )


if __name__ == "__main__":
    unittest.main()
