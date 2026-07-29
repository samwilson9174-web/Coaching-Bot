"""
main.py — entrypoint.

Modes:
  python -m coachbot.main run            # run once now (on-demand)
  python -m coachbot.main run --force    # run now, ignore 'already sent today'
  python -m coachbot.main schedule       # daily scheduler + health endpoint (Railway)
  python -m coachbot.main health         # health endpoint only
  python -m coachbot.main ib             # compute IB commissions + write report
  python -m coachbot.main report         # per-client trade report w/ market context
  python -m coachbot.main report --force # ignore 'already sent today' for reports

Railway: set start command to `python -m coachbot.main schedule`.
The health server keeps the web service alive and lets Railway health-check it.
"""
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from .config import config
from .logger import get_logger
from .pipeline import run_once

log = get_logger("main")

_last_run = {"time": None, "summary": None}


# --------------------------- health endpoint -------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = (
                '{"status":"ok","last_run":"%s"}'
                % (_last_run["time"] or "never")
            ).encode()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # silence default logging


def start_health_server():
    server = HTTPServer(("0.0.0.0", config.PORT), HealthHandler)
    log.info("Health server on :%d", config.PORT)
    server.serve_forever()


# --------------------------- scheduler -------------------------------------
def _do_run(force=False):
    log.info("=== run start ===")
    summary = run_once(force=force)
    _last_run["time"] = datetime.now(timezone.utc).isoformat()
    _last_run["summary"] = summary
    log.info("=== run done ===")
    return summary


def scheduler_loop():
    log.info("Scheduler armed for daily run at %s (UTC compare)", config.RUN_AT)
    fired_for = None
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if config.SCHEDULE_ENABLED and now == config.RUN_AT and fired_for != today:
            fired_for = today
            try:
                _do_run()
            except Exception as e:
                log.exception("Scheduled run failed: %s", e)
        time.sleep(20)


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "run"
    force = "--force" in args

    if mode == "run":
        _do_run(force=force)
    elif mode == "schedule":
        # health server in a daemon thread; scheduler blocks main thread
        threading.Thread(target=start_health_server, daemon=True).start()
        scheduler_loop()
    elif mode == "health":
        start_health_server()
    elif mode == "report":
        from .report import run_reports
        run_reports(force="--force" in sys.argv)
    elif mode == "ib":
        from .ib_pipeline import run_ib_commissions
        out = run_ib_commissions(write_report=True)
        roll = out["summary"]
        print(f"\nIB commission run — {roll['ib_count']} IBs, "
              f"{roll['ibs_with_activity']} with activity, "
              f"total ${roll['total_commission']:.2f} on "
              f"{roll['total_direct_lots']:.2f} lots\n")
        for r in sorted(out["results"].values(),
                        key=lambda x: x["total_commission"], reverse=True):
            if r["total_commission"] <= 0:
                continue
            print(f"  {r['name']:<22} ({r['tier'] or 'IB':<9}) "
                  f"direct ${r['direct_commission']:>9.2f}  "
                  f"downline ${r['downline_commission']:>9.2f}  "
                  f"= ${r['total_commission']:>9.2f}  "
                  f"[{r['active_client_count']}/{r['client_count']} clients]")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
