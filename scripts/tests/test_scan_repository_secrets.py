from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scan_repository_secrets", ROOT / "scripts" / "scan_repository_secrets.py")
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class SecretScannerTests(unittest.TestCase):
    def test_rejects_jwt_and_generic_credential_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unsafe.txt"
            source.write_text(
                "pass" + "word = \"actual-value-123\"\n" +
                "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue\n",
                encoding="utf-8",
            )
            findings = scanner.scan(root, [source])
        self.assertTrue(any("credential assignment" in item for item in findings))
        self.assertTrue(any("JWT-like" in item for item in findings))

    def test_allows_documented_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe.txt"
            source.write_text("password: ${RHA_PASSWORD}\ntoken: not-needed\napi_key: <your-key>\n", encoding="utf-8")
            self.assertEqual([], scanner.scan(root, [source]))

    def test_rejects_unquoted_compound_credential_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unsafe.env"
            source.write_text(
                "app_password=actual-value-123\n"
                "jwt_secret: signing-secret-456\n"
                "access_token = token-value-789\n",
                encoding="utf-8",
            )
            findings = scanner.scan(root, [source])
        self.assertEqual(3, sum("credential assignment" in item for item in findings))

    def test_allows_unquoted_environment_lookups_comments_and_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe.txt"
            source.write_text(
                "jwt_secret: ${RHA_JWT_SECRET}\n"
                "access_token = $env:RHA_ACCESS_TOKEN\n"
                "# password: documented-example-123\n"
                "Rotate the access token before production deployment.\n",
                encoding="utf-8",
            )
            self.assertEqual([], scanner.scan(root, [source]))


if __name__ == "__main__":
    unittest.main()
