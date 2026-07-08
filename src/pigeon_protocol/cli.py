from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from pigeon_protocol.config import LIVE_CAPTURES, REFERENCE_CAPTURES
from pigeon_protocol.service import PigeonProtocolService


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(name)s] %(message)s")


def cmd_status(svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    print(json.dumps(svc.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_extract_session(svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    path = svc.refresh_session_from_captures()
    print(f"session written: {path}")
    print(json.dumps(svc.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_index(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.capture_loader import index_captures

    idx = index_captures()
    print(json.dumps(
        {
            "ws_created": len(idx.ws_created),
            "ws_received": len(idx.ws_received),
            "ws_sent": len(idx.ws_sent),
            "http_bodies": len(idx.http_bodies),
            "order_requests": len(idx.order_requests),
            "history_requests": len(idx.history_requests),
            "conv_list_requests": len(idx.conv_list_requests),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_replay(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 1

    def _print(msg) -> None:
        print(json.dumps(msg.__dict__, ensure_ascii=False, default=str))

    count = svc.replay(path, _print)
    print(f"parsed messages: {count}")
    return 0


def cmd_listen(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    def _print(msg) -> None:
        print(json.dumps(msg.__dict__, ensure_ascii=False, default=str))

    asyncio.run(svc.listen(_print, timeout_sec=args.timeout))
    return 0


def _runtime_client(svc: PigeonProtocolService, *, live: bool = False):
    """Pick runtime: StandaloneRuntime > PureProtocolRuntime > service."""
    import os

    from pigeon_protocol.config import AppConfig

    cfg = AppConfig(dry_run=not live)
    if os.getenv("PIGEON_STANDALONE", "").strip().lower() in ("1", "true", "yes"):
        from pigeon_protocol.standalone import StandaloneRuntime

        return StandaloneRuntime(config=cfg)
    if live:
        from pigeon_protocol.pure_runtime import PureProtocolRuntime

        return PureProtocolRuntime(config=cfg)
    return None


def cmd_context(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    client = _runtime_client(svc, live=args.live)
    if client is not None:
        ctx = client.get_context(
            security_user_id=args.user_id or "",
            conversation_id=args.conversation_id or "",
            use_cdp_fallback=bool(args.cdp),
        )
        print(svc.dump_json(ctx))
        return 0
    if args.cdp:
        svc.context.http.use_cdp_sign = True
    ctx = svc.context.get_context(
        conversation_id=args.conversation_id or "",
        security_user_id=args.user_id or "",
        via_pigeon_im=args.cdp and bool(args.user_id),
    )
    print(svc.dump_json(ctx))
    return 0


def cmd_orders(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    if not args.user_id:
        print("--user-id required (AQ... security_user_id)", file=sys.stderr)
        return 1
    client = _runtime_client(svc, live=args.live)
    if client is not None:
        result = client.get_orders(
            args.user_id,
            use_cdp_fetch=bool(args.cdp),
            use_cdp_fallback=bool(args.cdp),
        )
        print(svc.dump_json(result))
        return 0 if result.has_order or "cache" in (result.source or "") or "user_card" in (result.source or "") else 1
    if args.cdp:
        svc.orders.http.use_cdp_sign = True
    result = svc.orders.get_orders(args.user_id, via_cdp=args.cdp)
    print(svc.dump_json(result))
    code = ""
    raw = result.raw if hasattr(result, "raw") else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    code = str(data.get("code", ""))
    if args.cdp and code == "10001010A":
        return 1
    if not args.cdp and not raw.get("ok", True) and not result.has_order:
        return 1
    return 0


def cmd_cdp_probe(svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.cdp_bridge import CdpBridge, cdp_ready

    info = {"cdp_ready": cdp_ready(), "session_cookies": len(svc.session.cookies)}
    if cdp_ready():
        bridge = CdpBridge(svc.session)
        info["bdms"] = asyncio.run(bridge.probe_bdms())
        info["query_tokens"] = {k: v[:48] for k, v in svc.session.query_tokens.items() if k in ("verifyFp", "msToken", "a_bogus")}
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0 if info["cdp_ready"] else 1


def cmd_send(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    client = _runtime_client(svc, live=args.live)
    if client is not None:
        result = client.send_text(
            args.text,
            conversation_id=args.conversation_id or "",
            security_user_id=args.user_id or "",
        )
        print(svc.dump_json(result))
        return 0 if result.ok else 1
    svc.config.dry_run = not args.live
    svc.sender.dry_run = not args.live
    result = svc.sender.send_text(
        args.text,
        conversation_id=args.conversation_id or "",
        security_user_id=args.user_id or "",
        seq=args.seq,
        handshake=not args.no_handshake,
        replay_exact=args.replay_exact,
    )
    print(svc.dump_json(result))
    return 0 if result.ok else 1


def cmd_build_send(svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    payload = svc.sender.build_payload(args.text, seq=args.seq)
    print(json.dumps({"length": len(payload), "hex_prefix": payload[:80].hex()}, ensure_ascii=False, indent=2))
    return 0


def cmd_har_order(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.har_replay import order_from_har

    result = order_from_har()
    print(json.dumps(result.__dict__ if result else {}, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_prepare(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.pure_runtime import PureProtocolRuntime

    client = PureProtocolRuntime(config=_svc.config)
    report = client.prepare(force_cdp=not args.no_cdp and CdpSessionSync_available())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("mode") != "session_only" or client.health()["cookies"] else 1


def cmd_list_send_templates(svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    print(json.dumps(svc.sender.list_supported_lengths(), ensure_ascii=False, indent=2))
    return 0


def cmd_standalone_status(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import os

    os.environ["PIGEON_STANDALONE"] = "1"
    from pigeon_protocol.standalone import StandaloneRuntime

    client = StandaloneRuntime(config=_svc.config)
    print(json.dumps(client.health(), ensure_ascii=False, indent=2))
    return 0


def cmd_bootstrap(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.pure_runtime import PureProtocolRuntime

    client = PureProtocolRuntime(config=_svc.config)
    report = client.bootstrap(
        prepare=not args.skip_prepare,
        harvest=not args.skip_harvest,
        quick=not args.full,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    still = (report.get("template_harvest") or {}).get("still_missing") or report.get("template_missing_before") or []
    if still and not args.allow_partial:
        return 1 if args.skip_harvest else 0
    return 0


def cmd_pull_ws_capture(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import subprocess

    wait = getattr(args, "timeout", 45)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[2] / "scripts" / "pull_ws_capture.py"), str(wait)],
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    return proc.returncode


def cmd_pure_status(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.pure_runtime import PureProtocolRuntime

    client = PureProtocolRuntime(config=_svc.config)
    print(json.dumps(client.health(), ensure_ascii=False, indent=2))
    return 0


def cmd_foundation_status(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.foundation.status import foundation_report
    from pigeon_protocol.session import load_session

    report = foundation_report(load_session())
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def cmd_decompile_169b(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, str(root / "scripts" / "decompile_169b_inner.py")], cwd=str(root))
    return proc.returncode


def cmd_bdms_jsvmp_parse(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.foundation.bdms_jsvmp import deep_report, load_program

    prog, meta = load_program()
    report = {"meta": meta, **deep_report(prog)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_bdms_sign_pipeline(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.foundation.bdms_sign_pipeline import pipeline_report
    from pigeon_protocol.foundation.bdms_jsvmp_disasm import disasm_function, sign_flow_summary
    from pigeon_protocol.foundation.bdms_jsvmp import load_program

    prog, _ = load_program()
    report = pipeline_report(prog)
    if getattr(args, "fn", None) is not None:
        fi = args.fn
        report = {
            "fn": fi,
            "sign_flow": sign_flow_summary(prog, fi),
            "disasm": disasm_function(prog, fi)["disasm_text"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_abogus_test(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import base64
    import re
    import subprocess
    from pathlib import Path
    from urllib.parse import urlparse

    from pigeon_protocol.foundation.bdms_abogus import FeigeABogus
    from pigeon_protocol.foundation.bdms_sign import extract_tokens, sign_backstage_url
    from pigeon_protocol.session import load_session

    def payload_bytes(s: str) -> int:
        pad = "=" * ((4 - len(s) % 4) % 4)
        t = re.sub(r"[^A-Za-z0-9+/=_-]", "", s).replace("-", "+").replace("_", "/")
        return len(base64.b64decode(t + pad))

    root = Path(__file__).resolve().parents[2]
    url = (
        "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
        "?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true&_v=1.0.1.7626"
    )
    q = urlparse(url).query
    feige = FeigeABogus()
    py = feige.sign_query(q)
    py_sign = sign_backstage_url(url, prefer_python=True)
    node_proc = subprocess.run(
        ["node", str(root / "scripts" / "run_bdms_fetch.mjs"), url, "", "GET"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    node_raw = json.loads(node_proc.stdout) if node_proc.stdout.strip() else {}
    node_t = extract_tokens(node_raw, fallback=url)
    node_ab = node_t.get("a_bogus", "")
    report = {
        "python_len": len(py),
        "python_payload_bytes": payload_bytes(py),
        "python_fp_len": len(feige.browser_fp),
        "python_via": py_sign.via,
        "node_len": len(node_ab),
        "node_payload_bytes": payload_bytes(node_ab) if node_ab else 0,
        "payload_bytes_match": payload_bytes(py) == payload_bytes(node_ab) if node_ab else False,
        "session_msToken": bool(load_session().query_tokens.get("msToken")),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_gap_workstreams(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_gap_workstreams.py")],
        cwd=str(root),
    )
    return proc.returncode


def cmd_audit_foundation(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "audit_foundation.py")],
        cwd=str(root),
    )
    return proc.returncode


def cmd_import_har_ws(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, str(root / "scripts" / "import_har_ws_templates.py")]
    if args.file:
        cmd.extend(["--file", args.file])
    if args.from_captures:
        cmd.append("--from-captures")
    if args.overwrite:
        cmd.append("--overwrite")
    proc = subprocess.run(cmd, cwd=str(root))
    return proc.returncode


def cmd_harvest_long_ws(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, str(root / "scripts" / "harvest_ws_long_samples.py"), "--port", str(args.port)]
    if args.lengths:
        cmd.extend(["--lengths", args.lengths])
    if args.timeout:
        cmd.extend(["--timeout", str(args.timeout)])
    if args.delay:
        cmd.extend(["--delay", str(args.delay)])
    if args.dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=str(root))
    return proc.returncode


def cmd_ws_gap_plan(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.capture_loader import index_send_templates
    from pigeon_protocol.ws_sign_bucket import coverage_report, gap_harvest_plan
    from pigeon_protocol.ws_template_harvest import text_for_byte_length

    pool = index_send_templates()
    plan = gap_harvest_plan()
    priority = plan.get("harvest_priority") or []
    rows = []
    for bl in priority[:24]:
        rows.append(
            {
                "textB": bl,
                "has_template": bl in pool,
                "send_text": text_for_byte_length(bl),
                "utf8_bytes": len(text_for_byte_length(bl).encode("utf-8")),
            }
        )
    report = {
        "coverage_1_200": coverage_report().get("supported_count_1_200"),
        "pool_lengths": sorted(pool.keys()),
        "priority": rows,
        "harvest_cmd": "Send texts in Feige → Save HAR → python run.py import-har-ws --file ws_send.har",
        "doc": "docs/CAPTURE_CHECKLIST.md",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_import_cookies(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.cookie_import import import_cookies

    from pigeon_protocol.account_context import session_file

    session = import_cookies(
        args.file,
        merge=not args.replace,
        shop_id=args.shop_id or "",
        user_agent=args.user_agent or "",
    )
    print(json.dumps(
        {
            "cookies": len(session.cookies),
            "shop_id": session.shop_id,
            "session_file": str(session_file()),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_import_har(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.har_session_import import import_har_session

    path = Path(args.file)
    if not path.exists():
        print(f"HAR not found: {path}", file=sys.stderr)
        return 1
    report = import_har_session(
        path,
        merge=not args.replace,
        run_parse=not args.no_captures,
        refresh_csrf_after=not args.no_csrf_refresh,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("cookies", 0) > 0:
        return 0
    if report.get("warnings"):
        return 0
    return 2


def cmd_serve_api(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.api_server import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_go_bridge(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.go_bridge import main as bridge_main, run_daemon

    if getattr(args, "daemon", False):
        return run_daemon()
    return bridge_main()


def cmd_refresh_conv_snapshot(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.conv_sign_snapshot import has_fresh_snapshot, refresh_snapshots_from_cdp
    from pigeon_protocol.session import load_session

    session = load_session()
    if args.cdp_only:
        report = refresh_snapshots_from_cdp(session)
    else:
        from pigeon_protocol.conv_list import list_conversations_relay

        report = list_conversations_relay(session, size=args.size)
        report = {
            "ok": report.get("ok"),
            "via": report.get("via"),
            "items": len(report.get("items") or []),
            "snapshot_fresh": has_fresh_snapshot(),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_refresh_ws_token(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.feige_init import _fetch_get_link_info
    from pigeon_protocol.session import load_session, save_session

    session = load_session()
    report = _fetch_get_link_info(session)
    try:
        save_session(session)
    except OSError:
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_session_doctor(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_health import auto_heal_session, check_session

    session = load_session()
    if args.fix:
        try:
            from pigeon_protocol.session_portable import ensure_portable_ready

            portable = ensure_portable_ready(session, heal=True)
            print(json.dumps({"portable": portable}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"portable_error": str(exc)}, ensure_ascii=False))
        health = auto_heal_session(session, refresh_csrf=True)
        try:
            save_session(session)
        except OSError:
            pass
    else:
        health = check_session(session)
    print(json.dumps(health.to_dict(), ensure_ascii=False, indent=2))
    return 0 if health.ok else 1


def cmd_export_session_pack(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.account_context import session_pack_file
    from pigeon_protocol.session_portable import export_session_pack

    dest = Path(args.file) if str(args.file or "").strip() else session_pack_file()
    report = export_session_pack(dest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_import_session_pack(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.session_portable import import_session_pack

    report = import_session_pack(
        Path(args.file),
        run_prepare=not args.no_prepare,
        set_active=bool(args.set_active),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_cdp_warm_inners(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import asyncio
    import json

    from pigeon_protocol.cdp_warm_inners import warm_session_inners_async

    report = asyncio.run(warm_session_inners_async(launch=False))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_cdp_onboard(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import json

    from pigeon_protocol.cdp_onboard import run_onboard, write_report
    from pigeon_protocol.runtime_paths import apply_runtime_env

    apply_runtime_env()
    report = run_onboard(
        wait_sec=float(args.wait),
        launch=not args.no_launch,
        close_browser=not args.keep_browser,
        warm_inners=not args.no_warm,
        export_pack=not args.no_export,
        background=False,
    )
    write_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_cdp_warm(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import json
    import time

    from pigeon_protocol.cdp_onboard import start_warm_background, warm_job_snapshot
    from pigeon_protocol.runtime_paths import apply_runtime_env

    apply_runtime_env()
    out = start_warm_background(launch=not args.no_launch)
    if args.wait:
        deadline = time.time() + float(args.wait)
        while time.time() < deadline:
            snap = warm_job_snapshot()
            if not snap.get("running"):
                break
            time.sleep(1.0)
        out = warm_job_snapshot()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("phase") == "done" or out.get("send_ready") else 1


def cmd_session_renew(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import json

    from pigeon_protocol.runtime_paths import apply_runtime_env
    from pigeon_protocol.session import load_session, save_session
    from pigeon_protocol.session_renewal import establish_im_session_http, renew_session_if_needed

    apply_runtime_env()
    session = load_session()
    if args.full:
        report = establish_im_session_http(session, persist=True)
    else:
        report = renew_session_if_needed(session, persist=True)
    save_session(session)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_session_bootstrap(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    import json

    from pigeon_protocol.runtime_paths import apply_runtime_env
    from pigeon_protocol.session_startup import bootstrap_on_startup

    apply_runtime_env()
    report = bootstrap_on_startup(
        auto_import_pack=not args.no_import,
        export_if_ready=not args.no_export,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_send_smoke(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.setdefault("PIGEON_STANDALONE", "1")
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "test_live_send_pure.py")],
        cwd=str(root),
        env=env,
    )
    return proc.returncode


def cmd_pure_algo_verify(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, str(root / "scripts" / "verify_pure_algo_fixes.py")], cwd=str(root))
    return proc.returncode


def cmd_sdk_probe(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, str(root / "scripts" / "cdp_probe_sdk_invoke.py")], cwd=str(root))
    return proc.returncode


def _run_script(name: str) -> int:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, str(root / "scripts" / name)], cwd=str(root))
    return proc.returncode


def _run_node_script(name: str) -> int:
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(["node", str(root / "scripts" / name)], cwd=str(root))
    return proc.returncode


def cmd_feige_export_sdk(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    return _run_script("export_feige_rust_sdk.py")


def cmd_feige_rust_probe(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    return _run_node_script("probe_feige_rust_sdk.mjs")


def cmd_feige_electron_probe(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    return _run_script("feige_electron_cdp_probe.py")


def cmd_feige_rust_invoke(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    return _run_script("feige_rust_invoke.py")


cmd_rust_sdk_invoke = cmd_feige_rust_invoke


def cmd_conv_cdp(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.conv_list import parse_conversation_items
    from pigeon_protocol.conv_list_cdp import list_conversations_cdp
    from pigeon_protocol.session import load_session

    session = load_session()
    raw = list_conversations_cdp(
        session,
        size=args.size,
        auto_launch=not args.no_launch,
        wait_login_sec=args.wait_login,
        inject_session_cookies=not args.no_inject,
    )
    items = raw.get("items") or parse_conversation_items(raw)
    print(
        json.dumps(
            {
                "ok": bool(raw.get("ok") and items),
                "via": raw.get("via"),
                "count": len(items),
                "warm": raw.get("warm"),
                "attempts": raw.get("attempts"),
                "sample": items[:5],
                "error": raw.get("error"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if items else 1


def cmd_prepare_pure(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.foundation.pure_prepare import prepare_pure_runtime
    from pigeon_protocol.session import load_session

    session = load_session()
    report = prepare_pure_runtime(session, probe_ws=args.probe_ws)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_refresh_csrf(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.session import load_session
    from pigeon_protocol.secsdk_csrf import refresh_relay_headers

    session = load_session()
    hdr = refresh_relay_headers(session, persist=True)
    print(
        json.dumps(
            {
                "ok": bool(hdr.get("x-secsdk-csrf-token")),
                "csrf_prefix": (hdr.get("x-secsdk-csrf-token") or "")[:64],
                "keys": sorted(hdr.keys()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if hdr.get("x-secsdk-csrf-token") else 1


def cmd_demo(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.pure_runtime import PureProtocolRuntime

    if not args.user_id:
        print("--user-id required (AQ... security_user_id)", file=sys.stderr)
        return 1
    client = PureProtocolRuntime(config=_svc.config)
    report = client.run_demo(
        args.user_id,
        listen_sec=args.listen,
        send_text=args.text or "",
        skip_prepare=args.skip_prepare,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    orders = report.get("orders") or {}
    ctx = report.get("context") or {}
    ok = bool(orders.get("has_order") or ctx.get("message_count", 0) > 0)
    return 0 if ok or args.allow_fail else 1


def CdpSessionSync_available() -> bool:
    from pigeon_protocol.session_sync import CdpSessionSync

    return CdpSessionSync.available()


def cmd_har_context(_svc: PigeonProtocolService, _args: argparse.Namespace) -> int:
    from pigeon_protocol.har_replay import context_from_har

    result = context_from_har()
    print(json.dumps(result.__dict__ if result else {}, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_qr_login(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pathlib import Path

    from pigeon_protocol.qr_login import (
        DoudianSsoQrLoginClient,
        qr_login_to_session,
    )

    har_path = Path(args.har).expanduser() if args.har else None
    qrcode_path = Path(args.qrcode).expanduser() if args.qrcode else None

    if args.fetch_only:
        client = DoudianSsoQrLoginClient(har_path=har_path)
        state = client.fetch_qrcode()
        from pigeon_protocol.account_context import qr_png_path

        out = qrcode_path or qr_png_path()
        if state.qrcode_b64:
            out.parent.mkdir(parents=True, exist_ok=True)
            import base64

            out.write_bytes(base64.b64decode(state.qrcode_b64))
        report = {"qr": state.to_dict(), "qrcode_path": str(out), "ok": not state.error}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not state.error else 1

    report = qr_login_to_session(
        qrcode_path=qrcode_path,
        timeout_sec=float(args.timeout),
        har_path=har_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_workbench(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.workbench import PureWorkbench, run_workbench

    if args.bootstrap_only:
        report = run_workbench(listen=False, bootstrap_only=True, conv_refresh_sec=0)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("current_uid") else 1

    wb = PureWorkbench(conv_refresh_sec=args.conv_refresh)
    if args.no_listen:
        report = wb.bootstrap()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("current_uid") else 1

    try:
        wb.run(listen=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_accounts(_svc: PigeonProtocolService, args: argparse.Namespace) -> int:
    from pigeon_protocol.account_context import (
        account_status,
        create_account_slot,
        list_accounts,
        switch_account,
    )

    action = args.accounts_action
    if action == "list":
        print(json.dumps(account_status(), ensure_ascii=False, indent=2))
        return 0
    if action == "switch":
        if not args.account_id:
            print(json.dumps({"ok": False, "error": "account_id required"}, ensure_ascii=False))
            return 1
        result = switch_account(args.account_id)
        print(json.dumps({**result, "accounts": list_accounts()}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if action == "create":
        aid = create_account_slot(label=args.label or "新账号")
        print(json.dumps({"ok": True, "account_id": aid, "accounts": list_accounts()}, ensure_ascii=False, indent=2))
        return 0
    return 1


def _apply_cli_account(args: argparse.Namespace) -> None:
    from pigeon_protocol.account_context import init_account_context, switch_account

    init_account_context(migrate=True)
    if getattr(args, "command", "") == "accounts" and getattr(args, "accounts_action", "") in ("switch", "create"):
        return
    aid = str(getattr(args, "account", "") or "").strip()
    if aid:
        switch_account(aid)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖店飞鸽纯协议实验客户端")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--live", action="store_true", help="disable dry-run for HTTP/WS send")
    parser.add_argument("--cdp", action="store_true", help="use Chrome CDP page fetch (bdms auto-sign)")
    parser.add_argument(
        "--account",
        default="",
        help="active account id (accounts/shop_<id> or acct_<hex>); see `accounts list`",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show session + capture index")
    sub.add_parser("extract-session", help="build session/session.json from captures")
    sub.add_parser("index-captures", help="count capture files by type")

    p_replay = sub.add_parser("replay", help="offline parse one capture json")
    p_replay.add_argument("--file", required=True)

    p_listen = sub.add_parser("listen", help="live WS listen (needs valid session)")
    p_listen.add_argument("--timeout", type=int, default=120)

    p_ctx = sub.add_parser("context", help="fetch conversation context via HTTP")
    p_ctx.add_argument("--conversation-id", default="")
    p_ctx.add_argument("--user-id", default="", help="security_user_id AQ...")

    p_ord = sub.add_parser("orders", help="fetch orders for security_user_id")
    p_ord.add_argument("--user-id", required=True)

    p_send = sub.add_parser("send", help="build/send WS text message")
    p_send.add_argument("--text", required=True)
    p_send.add_argument("--conversation-id", default="")
    p_send.add_argument("--user-id", default="", help="security_user_id AQ...")
    p_send.add_argument("--seq", type=int, default=None)
    p_send.add_argument("--no-handshake", action="store_true", help="skip inbox sync frames before send")
    p_send.add_argument("--replay-exact", action="store_true", help="replay HAR template bytes unchanged (sign RE test)")

    p_build = sub.add_parser("build-send", help="only build outbound protobuf payload")
    p_build.add_argument("--text", required=True)
    p_build.add_argument("--seq", type=int, default=None)

    sub.add_parser("har-order", help="replay order context from HAR captures")
    sub.add_parser("har-context", help="replay conversation context from HAR captures")
    sub.add_parser("cdp-probe", help="inspect Chrome CDP + bdms SDK")
    p_conv_cdp = sub.add_parser("conv-cdp", help="xundan conv list via Chrome CDP (auto-launch)")
    p_conv_cdp.add_argument("--size", type=int, default=30)
    p_conv_cdp.add_argument("--no-launch", action="store_true", help="do not auto-start Chrome")
    p_conv_cdp.add_argument("--wait-login", type=float, default=90.0, help="seconds to wait for Feige login in CDP Chrome")
    p_conv_cdp.add_argument("--no-inject", action="store_true", help="do not inject session.json cookies into CDP browser")

    p_prep = sub.add_parser("prepare", help="CDP one-shot sync session (cookies, ws url, sign tokens)")
    p_prep.add_argument("--no-cdp", action="store_true", help="skip CDP, only report current session")

    p_demo = sub.add_parser("demo", help="prepare + orders + context + optional listen/send")
    p_demo.add_argument("--user-id", required=True, help="security_user_id AQ...")
    p_demo.add_argument("--listen", type=int, default=10, help="WS listen seconds (0=skip)")
    p_demo.add_argument("--text", default="", help="optional WS text to send")
    p_demo.add_argument("--skip-prepare", action="store_true", help="use existing session.json")
    p_demo.add_argument("--allow-fail", action="store_true", help="exit 0 even if orders/context empty")

    p_pure = sub.add_parser("pure-status", help="pure protocol readiness matrix")
    sub.add_parser("list-send-templates", help="WS send template pool by text byte length")
    p_pull = sub.add_parser("pull-ws-capture", help="hook WS.send, capture templates (then send in Feige)")
    p_pull.add_argument("--timeout", type=int, default=45, help="seconds to wait for sends")

    sub.add_parser("standalone-status", help="browser-free readiness (PIGEON_STANDALONE=1)")
    sub.add_parser("foundation-status", help="protocol foundation / RE readiness matrix")
    sub.add_parser("decompile-169b", help="169B inner RE report — layout, corpus diff, Rust .node strings")
    sub.add_parser("bdms-jsvmp-parse", help="parse bdms jsvmp inflated VM (pure, no browser)")
    p_pipe = sub.add_parser("bdms-sign-pipeline", help="a_bogus sign pipeline static RE report")
    p_pipe.add_argument("--fn", type=int, default=None, help="disasm single VM function index")
    sub.add_parser("abogus-test", help="compare pure Python vs Node a_bogus lengths")
    sub.add_parser("gap-workstreams", help="run all algorithm gap probes → analysis/gap_workstreams_report.json")
    sub.add_parser("audit-foundation", help="pure-protocol algorithm parity checklist")
    p_hws = sub.add_parser("import-har-ws", help="import signed WS frames from HAR into template pool")
    p_hws.add_argument("--file", default="", help="HAR path with WebSocket send messages")
    p_hws.add_argument("--from-captures", action="store_true", help="scan captures/live/from_har")
    p_hws.add_argument("--overwrite", action="store_true")
    sub.add_parser("ws-gap-plan", help="WS textB gaps + exact texts to send in Feige")

    p_long = sub.add_parser("harvest-long-ws", help="CDP harvest WS templates for textB > 200")
    p_long.add_argument("--port", type=int, default=9222, help="Chrome CDP port")
    p_long.add_argument("--lengths", default="", help="comma-separated byte lengths")
    p_long.add_argument("--timeout", type=float, default=30.0)
    p_long.add_argument("--delay", type=float, default=2.0)
    p_long.add_argument("--dry-run", action="store_true")

    p_boot = sub.add_parser("bootstrap", help="CDP sync + auto-harvest WS templates + sign refresh")
    p_boot.add_argument("--skip-prepare", action="store_true")
    p_boot.add_argument("--skip-harvest", action="store_true", help="only sync session/sign, no UI sends")
    p_boot.add_argument("--full", action="store_true", help="harvest full length ladder (30+ messages)")
    p_boot.add_argument("--allow-partial", action="store_true", help="exit 0 even if some lengths missing")

    p_imp = sub.add_parser("import-cookies", help="import cookies into session.json")
    p_imp.add_argument("--file", required=True, help="cookie file (JSON / Netscape / header string)")
    p_imp.add_argument("--replace", action="store_true", help="replace session instead of merge")
    p_imp.add_argument("--shop-id", default="", help="optional shop_id")
    p_imp.add_argument("--user-agent", default="", help="optional user agent override")

    p_har = sub.add_parser("import-har", help="import login HAR → session + relayHeaders + captures")
    p_har.add_argument("--file", required=True, help="path to .har from Chrome DevTools")
    p_har.add_argument("--replace", action="store_true", help="replace session instead of merge")
    p_har.add_argument("--no-captures", action="store_true", help="skip parse_har capture export")
    p_har.add_argument("--no-csrf-refresh", action="store_true", help="skip auto HEAD csrf refresh")

    p_qr = sub.add_parser("qr-login", help="doudian-sso QR login → session.json (对齐 登录.har)")
    p_qr.add_argument("--har", default="", help="optional 登录.har for SSO template fields")
    p_qr.add_argument("--qrcode", default="", help="save QR PNG path (default logs/fxg_login_qr.png)")
    p_qr.add_argument("--timeout", type=int, default=180, help="poll seconds waiting for scan")
    p_qr.add_argument("--fetch-only", action="store_true", help="only fetch QR, do not poll")

    p_wb = sub.add_parser(
        "workbench",
        help="全自动：会话列表+当前买家+懒加载上下文/订单+WS监听（无需 buyer-id）",
    )
    p_wb.add_argument("--bootstrap-only", action="store_true", help="只拉列表和首条上下文/订单，不监听")
    p_wb.add_argument("--no-listen", action="store_true", help="同 bootstrap-only")
    p_wb.add_argument("--conv-refresh", type=int, default=60, help="会话列表刷新间隔秒，0=禁用")

    p_doc = sub.add_parser("session-doctor", help="check session health; --fix refreshes CSRF headers")
    p_doc.add_argument("--fix", action="store_true", help="auto-refresh relay CSRF headers")

    p_exp = sub.add_parser(
        "export-session-pack",
        help="export portable zip for another PC (session + WS inners + relay env)",
    )
    p_exp.add_argument(
        "--file",
        default="",
        help="output .zip or directory (default: active account pigeon_session_pack.zip)",
    )

    p_imp_pack = sub.add_parser(
        "import-session-pack",
        help="import portable pack on new PC — no browser, auto prepare-pure",
    )
    p_imp_pack.add_argument("--file", required=True, help="pigeon_session_pack.zip or folder")
    p_imp_pack.add_argument("--set-active", action="store_true", help="switch to imported account after import")
    p_imp_pack.add_argument("--no-prepare", action="store_true", help="skip prepare-pure warm-up")

    p_boot = sub.add_parser(
        "session-bootstrap",
        help="startup: auto-import pack + heal tokens + optional re-export",
    )
    p_boot.add_argument("--no-import", action="store_true", help="skip auto-import pigeon_session_pack.zip")
    p_boot.add_argument("--no-export", action="store_true", help="skip pack re-export when send_ready")
    p_renew = sub.add_parser("session-renew", help="pure HTTP renew pigeon backstage (no browser)")
    p_renew.add_argument("--full", action="store_true", help="force full IM hop chain")

    p_prep_pure = sub.add_parser(
        "prepare-pure",
        help="pure-protocol warm-up: auto_heal + WS bootstrap + bundle export (no CDP)",
    )
    p_prep_pure.add_argument("--probe-ws", action="store_true", help="probe WS connect after bootstrap")
    sub.add_parser("cdp-warm-inners", help="CDP: send 7 canonical lengths → session inner cache")
    p_cdp_onboard = sub.add_parser(
        "cdp-onboard",
        help="one-shot: launch Chrome → scan login → sync → warm 169B → export pack",
    )
    p_cdp_onboard.add_argument("--wait", type=float, default=300.0, help="seconds waiting for QR scan")
    p_cdp_onboard.add_argument("--no-launch", action="store_true", help="require CDP already on :9222")
    p_cdp_onboard.add_argument("--keep-browser", action="store_true", help="do not close Chrome after onboard")
    p_cdp_onboard.add_argument("--no-warm", action="store_true", help="skip 169B warm (cookies only)")
    p_cdp_onboard.add_argument("--no-export", action="store_true", help="skip session pack export")
    p_cdp_warm = sub.add_parser("cdp-warm", help="CDP warm 169B only (backstage already valid)")
    p_cdp_warm.add_argument("--no-launch", action="store_true", help="require CDP already on :9222")
    p_cdp_warm.add_argument("--wait", type=float, default=0, help="wait seconds for warm job")
    sub.add_parser("send-smoke", help="live pure WS send smoke test (3 texts, needs session+inner cache)")
    sub.add_parser("pure-algo-verify", help="verify pigeon_sign + inner seed + frontier bypass")
    sub.add_parser("sdk-probe", help="CDP probe webviewBridge / PigeonIMCreateMessage cmd 11327")
    sub.add_parser("feige-export-sdk", help="export Feige Electron @pigeon-sdk/rust-sdk to analysis/")
    sub.add_parser("feige-rust-probe", help="Node smoke-probe rust-sdk.win32-x64-msvc.node")
    sub.add_parser("feige-electron-probe", help="launch/probe Feige Electron CDP :9223 for webviewBridge")
    sub.add_parser(
        "feige-rust-invoke",
        help="pure HTTP + bundled rust-sdk invokeAsync 11327 (no browser/client)",
    )
    sub.add_parser(
        "rust-sdk-invoke",
        help="alias of feige-rust-invoke",
    )

    sub.add_parser("refresh-csrf", help="HEAD-fetch x-secsdk-csrf-token (no CDP)")
    sub.add_parser("refresh-ws-token", help="get_link_info → fresh WS token + pigeon_sign (pure HTTP)")
    p_conv_snap = sub.add_parser(
        "refresh-conv-snapshot",
        help="bootstrap xundan sign snapshot (CDP sign+curl or conv list relay)",
    )
    p_conv_snap.add_argument("--size", type=int, default=20)
    p_conv_snap.add_argument("--cdp-only", action="store_true", help="only CDP curl-relay bootstrap")
    p_go_bridge = sub.add_parser("go-bridge", help="JSON stdin/stdout RPC for Go desktop (internal)")
    p_go_bridge.add_argument("--daemon", action="store_true", help="line-delimited persistent worker")

    p_api = sub.add_parser("serve-api", help="start local HTTP API for desktop GUI")

    p_acct = sub.add_parser("accounts", help="multi-account registry (list/switch/create)")
    p_acct.add_argument("accounts_action", choices=("list", "switch", "create"))
    p_acct.add_argument("--account-id", default="", help="target account id for switch")
    p_acct.add_argument("--label", default="新账号", help="label for create")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8765)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    _apply_cli_account(args)

    from pigeon_protocol.config import AppConfig

    cfg = AppConfig(dry_run=not args.live)
    svc = PigeonProtocolService(cfg)

    handlers = {
        "status": cmd_status,
        "extract-session": cmd_extract_session,
        "index-captures": cmd_index,
        "replay": cmd_replay,
        "listen": cmd_listen,
        "context": cmd_context,
        "orders": cmd_orders,
        "send": cmd_send,
        "build-send": cmd_build_send,
        "har-order": cmd_har_order,
        "har-context": cmd_har_context,
        "cdp-probe": cmd_cdp_probe,
        "conv-cdp": cmd_conv_cdp,
        "prepare": cmd_prepare,
        "demo": cmd_demo,
        "pure-status": cmd_pure_status,
        "list-send-templates": cmd_list_send_templates,
        "pull-ws-capture": cmd_pull_ws_capture,
        "bootstrap": cmd_bootstrap,
        "standalone-status": cmd_standalone_status,
        "foundation-status": cmd_foundation_status,
        "decompile-169b": cmd_decompile_169b,
        "bdms-jsvmp-parse": cmd_bdms_jsvmp_parse,
        "bdms-sign-pipeline": cmd_bdms_sign_pipeline,
        "abogus-test": cmd_abogus_test,
        "gap-workstreams": cmd_gap_workstreams,
        "audit-foundation": cmd_audit_foundation,
        "import-har-ws": cmd_import_har_ws,
        "ws-gap-plan": cmd_ws_gap_plan,
        "harvest-long-ws": cmd_harvest_long_ws,
        "import-cookies": cmd_import_cookies,
        "import-har": cmd_import_har,
        "qr-login": cmd_qr_login,
        "workbench": cmd_workbench,
        "session-doctor": cmd_session_doctor,
        "session-bootstrap": cmd_session_bootstrap,
        "session-renew": cmd_session_renew,
        "export-session-pack": cmd_export_session_pack,
        "import-session-pack": cmd_import_session_pack,
        "prepare-pure": cmd_prepare_pure,
        "cdp-warm-inners": cmd_cdp_warm_inners,
        "cdp-onboard": cmd_cdp_onboard,
        "cdp-warm": cmd_cdp_warm,
        "send-smoke": cmd_send_smoke,
        "pure-algo-verify": cmd_pure_algo_verify,
        "sdk-probe": cmd_sdk_probe,
        "feige-export-sdk": cmd_feige_export_sdk,
        "feige-rust-probe": cmd_feige_rust_probe,
        "feige-electron-probe": cmd_feige_electron_probe,
        "feige-rust-invoke": cmd_feige_rust_invoke,
        "rust-sdk-invoke": cmd_rust_sdk_invoke,
        "refresh-csrf": cmd_refresh_csrf,
        "refresh-ws-token": cmd_refresh_ws_token,
        "refresh-conv-snapshot": cmd_refresh_conv_snapshot,
        "go-bridge": cmd_go_bridge,
        "serve-api": cmd_serve_api,
        "accounts": cmd_accounts,
    }
    return handlers[args.command](svc, args)


if __name__ == "__main__":
    raise SystemExit(main())
