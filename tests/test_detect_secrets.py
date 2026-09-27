import unittest
from scripts.detect_secrets import (
    check_forbidden_filenames,
    check_content_line,
    mask_secret,
)


class TestDetectSecrets(unittest.TestCase):
    def test_forbidden_filenames(self):
        forbidden = [
            ".env",
            ".env.local",
            ".env.production",
            "server.key",
            "cert.pem",
            "id_rsa",
            "credentials.json",
            "client_secrets.json",
        ]
        violations = check_forbidden_filenames(forbidden)
        self.assertEqual(len(violations), len(forbidden))

    def test_allowlisted_filenames(self):
        allowed = [".env.example", ".env.sample", ".env.template", "main.py", "README.md"]
        violations = check_forbidden_filenames(allowed)
        self.assertEqual(len(violations), 0)

    def test_detect_telegram_bot_token(self):
        # 9 digits + colon + 35 characters
        fake_token = 'token = "712345678:AAHq_1234567890abcdefghijklmnopqrst"'
        hits = check_content_line(fake_token)
        self.assertTrue(any(h[0] == "Telegram Bot Token" for h in hits))

    def test_detect_cloudflare_sync_key(self):
        line = "CLOUDFLARE_SYNC_KEY = 'super_secret_cloudflare_sync_key_1234'"
        hits = check_content_line(line)
        self.assertTrue(any(h[0] == "Cloudflare Sync Key / API Token" for h in hits))

        line_yaml = "CLOUDFLARE_SYNC_KEY: 'super_secret_cloudflare_sync_key_1234'"
        hits_yaml = check_content_line(line_yaml)
        self.assertTrue(any(h[0] == "Cloudflare Sync Key / API Token" for h in hits_yaml))

    def test_detect_scraper_api_key(self):
        line = 'SCRAPER_API_KEY = "abcdef0123456789abcdef0123456789"'
        hits = check_content_line(line)
        self.assertTrue(any(h[0] == "Scraper API Key" for h in hits))

        line_yaml = 'SCRAPER_API_KEY: "abcdef0123456789abcdef0123456789"'
        hits_yaml = check_content_line(line_yaml)
        self.assertTrue(any(h[0] == "Scraper API Key" for h in hits_yaml))

    def test_detect_private_key(self):
        line = "-----BEGIN RSA PRIVATE KEY-----"
        hits = check_content_line(line)
        self.assertTrue(any(h[0] == "Private Key Block" for h in hits))

    def test_detect_github_token(self):
        line = 'github_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz12"'
        hits = check_content_line(line)
        self.assertTrue(any(h[0] == "GitHub Personal Access Token" for h in hits))

    def test_allow_safe_env_references_and_placeholders(self):
        safe_lines = [
            'TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")',
            "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}",
            'TELEGRAM_BOT_TOKEN = "your_bot_token_here"',
            'SCRAPER_API_KEY = "placeholder_key"',
            'CLOUDFLARE_SYNC_KEY = "dummy_token"',
            'token = ""',
        ]
        for line in safe_lines:
            hits = check_content_line(line)
            self.assertEqual(hits, [], f"Line was falsely flagged: {line}")

    def test_mask_secret(self):
        masked = mask_secret("123456789:ABCDEF1234567890ABCDEF1234567890ABC")
        self.assertTrue(masked.startswith("1234"))
        self.assertTrue(masked.endswith("0ABC"))
        self.assertIn("***...***", masked)


if __name__ == "__main__":
    unittest.main()
