"""Integration tests — per-account bundle/snapshot isolation."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class AccountIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="pigeon-iso-")
        self._root = Path(self._tmpdir)
        self._env = dict(os.environ)

        import pigeon_protocol.account_context as ac

        ac.ACCOUNTS_ROOT = self._root / "accounts"
        ac.REGISTRY_FILE = ac.ACCOUNTS_ROOT / "registry.json"
        ac.LEGACY_SESSION_DIR = self._root / "session"
        ac.LEGACY_BUNDLE_DIR = self._root / "standalone_bundle"
        ac._initialized = False
        self.ac = ac

        self.ac.register_account("shop_a", label="A店", shop_id="111", set_active=True)
        self.ac.register_account("shop_b", label="B店", shop_id="222")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_conv_snapshot_isolated_per_account(self) -> None:
        from pigeon_protocol.conv_sign_snapshot import load_snapshot_doc, save_queue_snapshot

        self.ac.switch_account("shop_a")
        save_queue_snapshot(
            queue_key="no_order",
            url="https://example.test/a",
            headers={"User-Agent": "a"},
        )
        doc_a = load_snapshot_doc()
        self.assertEqual(doc_a["queues"]["no_order"]["url"], "https://example.test/a")

        self.ac.switch_account("shop_b")
        self.assertIsNone(load_snapshot_doc())
        save_queue_snapshot(
            queue_key="no_order",
            url="https://example.test/b",
            headers={"User-Agent": "b"},
        )
        doc_b = load_snapshot_doc()
        self.assertEqual(doc_b["queues"]["no_order"]["url"], "https://example.test/b")

        self.ac.switch_account("shop_a")
        doc_a2 = load_snapshot_doc()
        self.assertIsNotNone(doc_a2)
        self.assertEqual(doc_a2["queues"]["no_order"]["url"], "https://example.test/a")

    def test_order_snapshot_isolated_per_account(self) -> None:
        from pigeon_protocol.order_sign_snapshot import load_sign_snapshot, save_sign_snapshot

        self.ac.switch_account("shop_a")
        save_sign_snapshot(url="https://order/a", headers={"x": "1"}, sample_body={"security_user_id": "AQa"})
        self.ac.switch_account("shop_b")
        self.assertIsNone(load_sign_snapshot())
        save_sign_snapshot(url="https://order/b", headers={"x": "2"}, sample_body={"security_user_id": "AQb"})

        self.ac.switch_account("shop_a")
        snap = load_sign_snapshot()
        self.assertEqual(snap["url"], "https://order/a")

    def test_csrf_persist_isolated_per_account(self) -> None:
        from pigeon_protocol.secsdk_csrf import _bundle_env

        self.ac.switch_account("shop_a")
        path_a = _bundle_env()
        path_a.parent.mkdir(parents=True, exist_ok=True)
        path_a.write_text(json.dumps({"csrfHeader": "a-token", "relayHeadersTs": 1}), encoding="utf-8")

        self.ac.switch_account("shop_b")
        path_b = _bundle_env()
        self.assertNotEqual(path_a, path_b)
        self.assertFalse(path_b.is_file())

        path_b.parent.mkdir(parents=True, exist_ok=True)
        path_b.write_text(json.dumps({"csrfHeader": "b-token", "relayHeadersTs": 2}), encoding="utf-8")

        self.ac.switch_account("shop_a")
        doc = json.loads(_bundle_env().read_text(encoding="utf-8"))
        self.assertEqual(doc["csrfHeader"], "a-token")

    def test_session_backup_isolated_per_account(self) -> None:
        from pigeon_protocol.session import SessionState, save_session
        from pigeon_protocol.session_backup import backup_session

        self.ac.switch_account("shop_a")
        save_session(SessionState(cookies={"sessionid": "aaa"}, shop_id="111"))
        rep_a = backup_session(tag="t")
        self.assertTrue(rep_a.get("ok"))

        self.ac.switch_account("shop_b")
        save_session(SessionState(cookies={"sessionid": "bbb"}, shop_id="222"))
        rep_b = backup_session(tag="t")
        self.assertTrue(rep_b.get("ok"))
        self.assertNotEqual(rep_a["path"], rep_b["path"])


if __name__ == "__main__":
    unittest.main()
