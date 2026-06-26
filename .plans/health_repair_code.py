# /api/health -- Desktop startup self-check + repair
# ---------------------------------------------------------------------------

_HEALTH_CACHE: dict | None = None
_HEALTH_CACHE_TS: float = 0.0
_HEALTH_CACHE_TTL: float = 30.0  # seconds

# --- Repair helpers -------------------------------------------------------

def _find_hermes_binary() -> str | None:
    """Locate the hermes-agent-cn-runtime binary."""
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""),
                     "cn.org.hermesagent.desktop", "runtime", "versions"),
    ]
    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base), reverse=True):
            ver_dir = os.path.join(base, entry)
            if not os.path.isdir(ver_dir):
                continue
            for name in os.listdir(ver_dir):
                if "hermes-agent-cn-runtime" in name and name.endswith(".exe"):
                    return os.path.join(ver_dir, name)
    return None


def _start_gateway(timeout: float = 15.0) -> tuple:
    """Start gateway as a detached subprocess. Returns (ok, detail)."""
    bin_path = _find_hermes_binary()
    if not bin_path:
        return False, "hermes binary not found"
    try:
        proc = subprocess.Popen(
            [bin_path, "gateway", "run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        detail = "gateway started (pid={})".format(proc.pid)
        hermes_home = get_hermes_home()
        tick_path = hermes_home / "cron" / ".tick.lock"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if tick_path.exists() and (time.time() - tick_path.stat().st_mtime) < 30:
                detail += ", tick confirmed ({:.0f}s ago)".format(time.time() - tick_path.stat().st_mtime)
                break
            time.sleep(1)
        else:
            detail += ", tick not confirmed within timeout"
        return True, detail
    except Exception as e:
        return False, "failed to start gateway: {}".format(e)


def _strip_file_bom(filepath) -> tuple:
    """Strip UTF-8 BOM from a file."""
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
        if raw[:3] != b"\xef\xbb\xbf":
            return False, "no BOM found (already clean)"
        clean = raw[3:]
        bak = str(filepath) + ".bak"
        with open(bak, "wb") as f:
            f.write(raw)
        with open(filepath, "wb") as f:
            f.write(clean)
        return True, "BOM stripped, backup at {}".format(bak)
    except Exception as e:
        return False, "BOM strip failed: {}".format(e)


def _repair_archive_ui(emit=None):
    """Repair session_ui_state and gateway-session-map."""
    def _e(phase, item, **kw):
        if emit:
            emit(phase, item, **kw)

    hermes_home = get_hermes_home()
    ui_db = hermes_home / "desktop-ui.sqlite"
    state_db = hermes_home / "state.db"

    if not ui_db.exists():
        return 0, {"error": "desktop-ui.sqlite not found"}
    if not state_db.exists():
        return 0, {"error": "state.db not found"}

    fixed = 0
    details = {}
    import sqlite3

    conn_ui = sqlite3.connect(str(ui_db))
    conn_state = sqlite3.connect(str(state_db))

    try:
        state_rows = {}
        for row in conn_state.execute("SELECT id, archived, ended_at FROM sessions"):
            state_rows[row[0]] = row

        orphaned_ui = []
        for row in conn_ui.execute("SELECT session_id FROM session_ui_state"):
            sid = row[0]
            if sid not in state_rows:
                orphaned_ui.append(sid)
        if orphaned_ui:
            _e("repair", "archive_ui", action="Removing {} orphaned UI entries...".format(len(orphaned_ui)))
            for sid in orphaned_ui:
                conn_ui.execute("DELETE FROM session_ui_state WHERE session_id = ?", (sid,))
            conn_ui.commit()
            fixed += len(orphaned_ui)
            details["orphaned_ui_removed"] = len(orphaned_ui)

        cur = conn_ui.execute("SELECT value_json FROM ui_kv WHERE key = 'hermes:gateway-session-map'")
        row = cur.fetchone()
        if row:
            sm = json.loads(row[0])
            orphaned_map = {k: v for k, v in sm.items() if v not in state_rows}
            if orphaned_map:
                _e("repair", "archive_ui", action="Removing {} orphaned map entries...".format(len(orphaned_map)))
                for k in orphaned_map:
                    del sm[k]
                conn_ui.execute(
                    "UPDATE ui_kv SET value_json = ? WHERE key = 'hermes:gateway-session-map'",
                    (json.dumps(sm),)
                )
                conn_ui.commit()
                fixed += len(orphaned_map)
                details["orphaned_map_removed"] = len(orphaned_map)

        ui_sessions = {r[0] for r in conn_ui.execute("SELECT session_id FROM session_ui_state")}
        missing = [sid for sid in state_rows if sid not in ui_sessions]
        if missing:
            _e("repair", "archive_ui", action="Adding {} missing sessions to UI state...".format(len(missing)))
            now_ms = int(time.time() * 1000)
            for sid in missing:
                conn_ui.execute(
                    "INSERT OR IGNORE INTO session_ui_state (session_id, title_override, archived, pinned, tags_json, workspace_path, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "", 0, 0, "[]", "", now_ms)
                )
            conn_ui.commit()
            fixed += len(missing)
            details["missing_added"] = len(missing)

        details["total_fixed"] = fixed
        return fixed, details
    finally:
        conn_ui.close()
        conn_state.close()


def _clean_old_logs(hermes_home, max_age_days: int = 30, max_size_mb: int = 500):
    logs_dir = hermes_home / "logs"
    if not logs_dir.exists():
        return 0, "no logs directory"
    removed = 0
    cutoff = time.time() - max_age_days * 86400
    for f in logs_dir.iterdir():
        if not f.is_file():
            continue
        try:
            if f.stat().st_mtime < cutoff or f.stat().st_size > max_size_mb * 1024 * 1024:
                f.unlink()
                removed += 1
        except Exception:
            pass
    return removed, "removed {} old/oversized log files".format(removed)


def _run_health_checks(repair: bool = False, emit=None) -> dict:
    def _e(phase, item, **kw):
        if emit:
            emit(phase, item, **kw)

    global _HEALTH_CACHE, _HEALTH_CACHE_TS
    now = time.time()
    if not repair and _HEALTH_CACHE is not None and (now - _HEALTH_CACHE_TS) < _HEALTH_CACHE_TTL:
        return _HEALTH_CACHE

    checks: dict[str, dict] = {}
    warnings: list[str] = []
    fixed: list[str] = []
    repair_errors: list[str] = []

    hermes_home = get_hermes_home()
    cron_dir = hermes_home / "cron"
    jobs_path = cron_dir / "jobs.json"
    tick_lock_path = cron_dir / ".tick.lock"
    config_path_ = get_config_path()

    # 1. Cron scheduler
    _e("check", "cron_scheduler", status="checking")
    try:
        if tick_lock_path.exists():
            mtime = tick_lock_path.stat().st_mtime
            age_sec = now - mtime
            if age_sec < 120:
                checks["cron_scheduler"] = {"ok": True, "detail": "running (tick.lock updated {:.0f}s ago)".format(age_sec)}
                _e("check", "cron_scheduler", status="ok", detail=checks["cron_scheduler"]["detail"])
            else:
                checks["cron_scheduler"] = {"ok": False, "detail": "stale (tick.lock age: {:.0f}s)".format(age_sec)}
                warnings.append("Cron scheduler may be dead")
                _e("check", "cron_scheduler", status="failed", detail=checks["cron_scheduler"]["detail"])
                if repair:
                    _e("repair", "cron_scheduler", action="Starting gateway to revive cron scheduler...", progress=0)
                    gw_pid = get_running_pid()
                    if gw_pid is None:
                        ok, detail = _start_gateway(timeout=20.0)
                        if ok:
                            fixed.append("cron_scheduler")
                            _e("repair", "cron_scheduler", action=detail, progress=100, status="fixed")
                            checks["cron_scheduler"] = {"ok": True, "detail": detail}
                        else:
                            repair_errors.append("cron_scheduler: {}".format(detail))
                            _e("repair", "cron_scheduler", action="FAILED: {}".format(detail), progress=100, status="failed")
                    else:
                        _e("repair", "cron_scheduler", action="Gateway running (pid={}), waiting for tick...".format(gw_pid), progress=50)
                        deadline = time.time() + 20
                        while time.time() < deadline:
                            if tick_lock_path.exists() and (time.time() - tick_lock_path.stat().st_mtime) < 30:
                                fixed.append("cron_scheduler")
                                _e("repair", "cron_scheduler", action="Cron tick confirmed", progress=100, status="fixed")
                                checks["cron_scheduler"] = {"ok": True, "detail": "running (tick confirmed)"}
                                break
                            time.sleep(1)
                        else:
                            repair_errors.append("cron_scheduler: tick not confirmed after 20s")
                            _e("repair", "cron_scheduler", action="Tick not confirmed", progress=100, status="failed")
        else:
            checks["cron_scheduler"] = {"ok": False, "detail": "not initialized (no tick.lock)"}
            _e("check", "cron_scheduler", status="failed", detail=checks["cron_scheduler"]["detail"])
            if repair:
                _e("repair", "cron_scheduler", action="Starting gateway for cron init...", progress=0)
                ok, detail = _start_gateway(timeout=20.0)
                if ok:
                    fixed.append("cron_scheduler")
                    _e("repair", "cron_scheduler", action=detail, progress=100, status="fixed")
                    checks["cron_scheduler"] = {"ok": True, "detail": detail}
                else:
                    repair_errors.append("cron_scheduler: {}".format(detail))
                    _e("repair", "cron_scheduler", action="FAILED: {}".format(detail), progress=100, status="failed")
    except Exception as e:
        checks["cron_scheduler"] = {"ok": False, "detail": "error: {}".format(e)}
        _e("check", "cron_scheduler", status="error", detail=str(e))

    # 2. jobs.json
    _e("check", "jobs_json", status="checking")
    try:
        if jobs_path.exists():
            with open(jobs_path, "rb") as f:
                header = f.read(4)
            has_bom = header[:3] == b"\xef\xbb\xbf"
            if has_bom:
                checks["jobs_json"] = {"ok": False, "detail": "UTF-8 BOM detected!"}
                warnings.append("jobs.json has UTF-8 BOM")
                _e("check", "jobs_json", status="failed", detail=checks["jobs_json"]["detail"])
                if repair:
                    _e("repair", "jobs_json", action="Stripping UTF-8 BOM from jobs.json...", progress=0)
                    ok, detail = _strip_file_bom(jobs_path)
                    if ok:
                        fixed.append("jobs_json")
                        _e("repair", "jobs_json", action=detail, progress=100, status="fixed")
                        checks["jobs_json"] = {"ok": True, "detail": detail}
                    else:
                        repair_errors.append("jobs_json: {}".format(detail))
                        _e("repair", "jobs_json", action="FAILED: {}".format(detail), progress=100, status="failed")
            else:
                try:
                    with open(jobs_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    job_count = len(data.get("jobs", []))
                    enabled = sum(1 for j in data.get("jobs", []) if j.get("state") != "paused")
                    checks["jobs_json"] = {"ok": True, "detail": "valid JSON, {} jobs ({} enabled)".format(job_count, enabled)}
                    _e("check", "jobs_json", status="ok", detail=checks["jobs_json"]["detail"])
                except json.JSONDecodeError as je:
                    checks["jobs_json"] = {"ok": False, "detail": "invalid JSON: {}".format(je)}
                    _e("check", "jobs_json", status="failed", detail=checks["jobs_json"]["detail"])
        else:
            checks["jobs_json"] = {"ok": True, "detail": "no jobs.json yet"}
            _e("check", "jobs_json", status="ok", detail=checks["jobs_json"]["detail"])
    except Exception as e:
        checks["jobs_json"] = {"ok": False, "detail": "error: {}".format(e)}
        _e("check", "jobs_json", status="error", detail=str(e))

    # 3. config.yaml
    _e("check", "config_yaml", status="checking")
    try:
        if config_path_.exists():
            with open(config_path_, "rb") as f:
                header = f.read(4)
            has_bom = header[:3] == b"\xef\xbb\xbf"
            try:
                with open(config_path_, "r", encoding="utf-8-sig" if has_bom else "utf-8") as f:
                    yaml.safe_load(f)
                detail = "valid YAML" + (" (UTF-8 BOM)" if has_bom else "")
                checks["config_yaml"] = {"ok": True, "detail": detail}
                _e("check", "config_yaml", status="ok", detail=detail)
                if has_bom and repair:
                    _e("repair", "config_yaml", action="Stripping UTF-8 BOM from config.yaml...", progress=0)
                    ok, detail2 = _strip_file_bom(config_path_)
                    if ok:
                        fixed.append("config_yaml")
                        _e("repair", "config_yaml", action=detail2, progress=100, status="fixed")
            except yaml.YAMLError as ye:
                checks["config_yaml"] = {"ok": False, "detail": "invalid YAML: {}".format(ye)}
                _e("check", "config_yaml", status="failed", detail=str(ye))
        else:
            checks["config_yaml"] = {"ok": True, "detail": "no config.yaml yet"}
            _e("check", "config_yaml", status="ok", detail=checks["config_yaml"]["detail"])
    except Exception as e:
        checks["config_yaml"] = {"ok": False, "detail": "error: {}".format(e)}
        _e("check", "config_yaml", status="error", detail=str(e))

    # 4. Gateway
    _e("check", "gateway", status="checking")
    try:
        gateway_pid = get_running_pid()
        gateway_running = gateway_pid is not None
        if gateway_running:
            checks["gateway"] = {"ok": True, "detail": "running (pid={})".format(gateway_pid)}
            _e("check", "gateway", status="ok", detail=checks["gateway"]["detail"])
        else:
            is_desktop = os.getenv("HERMES_DESKTOP") == "1"
            checks["gateway"] = {"ok": True, "detail": "not running" + (" (dashboard mode)" if is_desktop else "")}
            _e("check", "gateway", status="ok" if is_desktop else "warning", detail=checks["gateway"]["detail"])
            if not is_desktop and repair:
                _e("repair", "gateway", action="Starting gateway...", progress=0)
                ok, detail = _start_gateway(timeout=20.0)
                if ok:
                    fixed.append("gateway")
                    _e("repair", "gateway", action=detail, progress=100, status="fixed")
                    checks["gateway"] = {"ok": True, "detail": detail}
                else:
                    repair_errors.append("gateway: {}".format(detail))
                    _e("repair", "gateway", action="FAILED: {}".format(detail), progress=100, status="failed")
    except Exception as e:
        checks["gateway"] = {"ok": False, "detail": "error: {}".format(e)}
        _e("check", "gateway", status="error", detail=str(e))

    # 5. Disk space
    _e("check", "disk_space", status="checking")
    try:
        import shutil
        usage = shutil.disk_usage(str(hermes_home))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1.0:
            checks["disk_space"] = {"ok": False, "detail": "{:.1f} GB free -- critically low!".format(free_gb)}
            _e("check", "disk_space", status="failed", detail=checks["disk_space"]["detail"])
            if repair:
                _e("repair", "disk_space", action="Cleaning old logs...", progress=0)
                removed, detail2 = _clean_old_logs(hermes_home, max_age_days=7, max_size_mb=100)
                fixed.append("disk_space")
                _e("repair", "disk_space", action=detail2, progress=100, status="fixed")
        else:
            checks["disk_space"] = {"ok": True, "detail": "{:.1f} GB free".format(free_gb)}
            _e("check", "disk_space", status="ok", detail=checks["disk_space"]["detail"])
    except Exception as e:
        checks["disk_space"] = {"ok": True, "detail": "unable to check"}
        _e("check", "disk_space", status="ok", detail=str(e))

    # 6. code_execution
    _e("check", "code_execution", status="checking")
    try:
        if config_path_.exists():
            config = yaml.safe_load(config_path_.read_text(encoding="utf-8"))
            mode = config.get("code_execution", {}).get("mode", "project")
            detail = "mode={}".format(mode)
            if mode == "host":
                detail += " (full filesystem access)"
            elif mode == "project":
                detail += " (workspace-scoped)"
            checks["code_execution"] = {"ok": True, "detail": detail}
        else:
            checks["code_execution"] = {"ok": True, "detail": "mode=project (default)"}
        _e("check", "code_execution", status="ok", detail=checks["code_execution"]["detail"])
    except Exception as e:
        checks["code_execution"] = {"ok": True, "detail": "unknown"}
        _e("check", "code_execution", status="ok", detail=str(e))

    # 7. Archive UI state
    _e("check", "archive_ui_state", status="checking")
    try:
        ui_db = hermes_home / "desktop-ui.sqlite"
        state_db = hermes_home / "state.db"
        if ui_db.exists() and state_db.exists():
            import sqlite3
            conn_u = sqlite3.connect(str(ui_db))
            conn_s = sqlite3.connect(str(state_db))
            try:
                ui_count = conn_u.execute("SELECT COUNT(*) FROM session_ui_state").fetchone()[0]
                state_count = conn_s.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                missing = state_count - ui_count
                if missing > 0:
                    checks["archive_ui_state"] = {"ok": False, "detail": "{} sessions in DB but missing from UI state ({}/{})".format(missing, ui_count, state_count)}
                    _e("check", "archive_ui_state", status="failed", detail=checks["archive_ui_state"]["detail"])
                    if repair:
                        _e("repair", "archive_ui_state", action="Repairing archive: {} missing from UI...".format(missing), progress=0)
                        n, det = _repair_archive_ui(emit=emit)
                        if n > 0:
                            fixed.append("archive_ui_state")
                            _e("repair", "archive_ui_state", action="Fixed {} issues: {}".format(n, det), progress=100, status="fixed")
                            checks["archive_ui_state"] = {"ok": True, "detail": "repaired ({} fixes)".format(n)}
                        else:
                            _e("repair", "archive_ui_state", action="No fixes applied", progress=100, status="failed")
                else:
                    checks["archive_ui_state"] = {"ok": True, "detail": "consistent ({} sessions, {} in UI)".format(state_count, ui_count)}
                    _e("check", "archive_ui_state", status="ok", detail=checks["archive_ui_state"]["detail"])
            finally:
                conn_u.close()
                conn_s.close()
        else:
            checks["archive_ui_state"] = {"ok": True, "detail": "no UI database (server mode)"}
            _e("check", "archive_ui_state", status="ok", detail="n/a")
    except Exception as e:
        checks["archive_ui_state"] = {"ok": False, "detail": "error: {}".format(e)}
        _e("check", "archive_ui_state", status="error", detail=str(e))

    all_ok = all(c.get("ok", False) for c in checks.values())

    result = {
        "ok": all_ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "warnings": warnings,
    }
    if repair:
        result["fixed"] = fixed
        result["repair_errors"] = repair_errors

    if not repair:
        _HEALTH_CACHE = result
        _HEALTH_CACHE_TS = now
    return result


@app.get("/api/health")
async def get_health(profile: Optional[str] = None):
    """Run system health self-checks (read-only)."""
    health_scope = None
    requested_profile = (profile or "").strip()
    if requested_profile and requested_profile.lower() != "current":
        health_scope = _config_profile_scope(requested_profile)
        health_scope.__enter__()
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run_health_checks, False, None)
    finally:
        if health_scope is not None:
            health_scope.__exit__(*sys.exc_info())


@app.post("/api/health/repair")
async def post_health_repair(request: Request):
    """Run health checks AND repair detected issues. Returns SSE stream."""
    import queue as _queue

    event_queue: _queue.Queue[dict] = _queue.Queue()

    def _emit(phase: str, item: str, **kw):
        event_queue.put({"phase": phase, "item": item, **kw})

    async def _event_generator():
        import threading as _threading
        repair_result = {}
        repair_done = _threading.Event()

        def _run_repair():
            nonlocal repair_result
            try:
                repair_result = _run_health_checks(repair=True, emit=_emit)
            except Exception as exc:
                _emit("error", "internal", detail=str(exc))
                repair_result = {"ok": False, "error": str(exc)}
            finally:
                _emit("done", "__done__", result=repair_result)
                repair_done.set()

        thread = _threading.Thread(target=_run_repair, daemon=True, name="health-repair")
        thread.start()

        while not repair_done.is_set() or not event_queue.empty():
            try:
                ev = event_queue.get(timeout=0.5)
                yield "event: {}\ndata: {}\n\n".format(
                    ev.get("phase", "unknown"),
                    json.dumps(ev, ensure_ascii=False, default=str)
                )
            except _queue.Empty:
                if repair_done.is_set():
                    break
                yield ": heartbeat\n\n"

        yield "event: done\ndata: {}\n\n".format(json.dumps(repair_result, ensure_ascii=False, default=str))

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
