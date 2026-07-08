"""Tests for multi-account path resolution and registry."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class AccountContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="pigeon-acct-")
        self._root = Path(self._tmpdir)
        self._env = dict(os.environ)
        os.environ["PIGEON_SESSION_DIR"] = str(self._root / "session")
        os.environ["PIGEON_BUNDLE_DIR"] = str(self._root / "standalone_bundle")
        os.environ["PIGEON_LOGS_DIR"] = str(self._root / "logs")
        os.environ.pop("PIGEON_ACCOUNT_ID", None)

        import pigeon_protocol.account_context as ac

        ac.ACCOUNTS_ROOT = self._root / "accounts"
        ac.REGISTRY_FILE = ac.ACCOUNTS_ROOT / "registry.json"
        ac.LEGACY_SESSION_DIR = self._root / "session"
        ac.LEGACY_BUNDLE_DIR = self._root / "standalone_bundle"
        ac._initialized = False
        self.ac = ac

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_derive_and_switch_paths(self) -> None:
        aid = self.ac.derive_account_id(shop_id="263636465")
        self.assertEqual(aid, "shop_263636465")
        self.ac.register_account(aid, label="测试店", shop_id="263636465", set_active=True)
        home = self.ac.account_home(aid)
        self.assertEqual(home, self.ac.ACCOUNTS_ROOT / aid)
        self.assertEqual(self.ac.session_file(), home / "session.json")
        self.assertEqual(self.ac.bundle_dir(), home / "bundle")
        self.assertEqual(self.ac.qr_png_path(), home / "logs" / "fxg_login_qr.png")

    def test_switch_updates_env(self) -> None:
        a1 = "shop_111"
        a2 = "shop_222"
        self.ac.register_account(a1, set_active=True)
        self.ac.register_account(a2)
        self.ac.switch_account(a2)
        self.assertEqual(self.ac.active_account_id(), a2)
        self.assertEqual(os.environ["PIGEON_SESSION_DIR"], str(self.ac.account_home(a2)))
        self.assertEqual(os.environ["PIGEON_BUNDLE_DIR"], str(self.ac.account_home(a2) / "bundle"))

    def test_legacy_migration(self) -> None:
        legacy = self._root / "session"
        legacy.mkdir(parents=True)
        (legacy / "session.json").write_text(
            json.dumps(
                {
                    "cookies": {"sessionid": "abc", "SHOP_ID": "999"},
                    "shop_id": "999",
                }
            ),
            encoding="utf-8",
        )
        report = self.ac.init_account_context(migrate=True)
        self.assertTrue(report.get("migration", {}).get("migrated"))
        self.assertEqual(self.ac.active_account_id(), "shop_999")
        migrated_home = self.ac.account_home("shop_999")
        self.assertTrue((migrated_home / "session.json").is_file())

    def test_resolve_import_legacy_pack(self) -> None:
        self.ac.register_account("shop_888", set_active=True)
        target = self.ac.resolve_import_target("session/session.json")
        self.assertEqual(target, self.ac.account_home("shop_888") / "session.json")
        bundle_target = self.ac.resolve_import_target("standalone_bundle/ws_inner_canonical.json")
        self.assertEqual(bundle_target, self.ac.account_home("shop_888") / "bundle" / "ws_inner_canonical.json")

    def test_resolve_import_rejects_traversal(self) -> None:
        self.ac.register_account("shop_888", set_active=True)
        with self.assertRaises(ValueError):
            self.ac.resolve_import_target("../session.json")

    def test_register_default_not_active(self) -> None:
        self.ac.register_account("shop_a", set_active=True)
        self.ac.register_account("shop_b")
        from pigeon_protocol.session import SessionState

        sess = SessionState(cookies={"sessionid": "x", "SHOP_ID": "999"}, shop_id="999")
        aid = self.ac.register_account_from_session(sess)
        self.assertEqual(aid, "shop_999")
        self.assertEqual(self.ac.active_account_id(), "shop_a")


if __name__ == "__main__":
    unittest.main()
