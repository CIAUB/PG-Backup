import hashlib, json, os, re, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pgb import installer as I


class InstallerRender(unittest.TestCase):
    def test_caddy_domain_and_ip(self):
        d = I.render_caddyfile(8787, "panel.example.com", 8443, "a@b.co")
        self.assertIn("panel.example.com:8443 {", d); self.assertNotIn("tls internal", d); self.assertIn("email a@b.co", d)
        self.assertIn("panel.example.com {", I.render_caddyfile(8787, "panel.example.com", 443))
        ip = I.render_caddyfile(8787, None, 8443)
        self.assertIn(":8443 {", ip); self.assertIn("tls internal", ip); self.assertIn("admin off", ip)

    def test_units(self):
        u = I.render_panel_unit(8787)
        self.assertIn("--host 127.0.0.1", u); self.assertIn('Environment="PGB_BACKUP_BIN=python3 /usr/local/bin/PG-Backup"', u)
        self.assertIn("NoNewPrivileges=yes", u)
        self.assertIn("caddy run --config", I.render_caddy_unit("/usr/bin/caddy"))

    def test_domain_regex(self):
        for ok in ("a.example.com", "x-y.io"):
            self.assertTrue(I.DOMAIN_RE.match(ok))
        for bad in ("localhost", "a b.com", "evil.com\nreverse_proxy x", "-a.com", "a..com"):
            self.assertFalse(I.DOMAIN_RE.match(bad), bad)


class ManifestUpToDate(unittest.TestCase):
    def test_manifest_matches_tree_and_script(self):
        raw = open(os.path.join(ROOT, "pgb-manifest.json"), "rb").read()
        m = json.loads(raw)
        for rel, dig in m["files"].items():
            self.assertEqual(hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest(), dig, rel + " changed: run tools/build_manifest.py")
        on_disk = set()
        for dp, dn, fns in os.walk(os.path.join(ROOT, "pgb")):
            dn[:] = [d for d in dn if d != "__pycache__"]
            on_disk |= {os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/") for f in fns if not f.endswith(".pyc")}
        self.assertEqual(on_disk, set(m["files"]), "file set differs: run tools/build_manifest.py")
        src = open(os.path.join(ROOT, "pg_backup.py"), encoding="utf-8").read()
        self.assertEqual(re.search(r'^PGB_MANIFEST_SHA256 = "([0-9a-f]{64})"', src, re.M).group(1), hashlib.sha256(raw).hexdigest())
        self.assertEqual(m["version"], re.search(r'^PGB_VERSION = "([^"]+)"', src, re.M).group(1))


if __name__ == "__main__":
    unittest.main()
