#!/usr/bin/env python3
"""Thermal Report web application (merged build).

With ORGS_FILE set, several groups share one installation: each person signs in through
Cloudflare Access, the app checks the signed token, and shows only the groups they belong to
(see orgs.py, auth.py). Without it, there is one group and no sign-in check.

Flow: upload FLIR images -> hot spots detected -> surveyor defines points (type + number),
adds extra points by clicking, and writes freehand notes -> heat loss report is generated
with both the auto-generated comments and the surveyor's notes.

All files go through the storage backend chosen with STORAGE_BACKEND
(local | webdav | smb), so the app runs unchanged on any server.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for, Response)
from werkzeug.local import LocalProxy
from werkzeug.utils import secure_filename
import io

from audit import AuditLog
from auth import build_identity
from inbox import Inbox, InboxError
from orgs import Org, OrgDirectory

from heat_loss_reporter import HeatLossReporter
from services import BatchError, BatchService, new_batch_id, valid_batch_id
from settings import load_settings
from wake import Waker
from vocab import (SENSITIVITY_LABELS, SENSITIVITY_ORDER, SPOT_ABBR, severity_legend)
from storage import (PrefixedStorage, StorageBackend, StorageError, StorageNotFound,
                     create_storage)

logger = logging.getLogger("thermal_report")

PUBLIC_ENDPOINTS = {"healthz", "static"}
REPORT_FILE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".csv": "text/csv",
                     ".html": "text/html"}


@dataclass
class Context:
    """Everything that belongs to one group (or to the single group of a one-group install)."""
    slug: str
    org_name: str
    storage: StorageBackend
    service: BatchService
    reporter: HeatLossReporter
    sensitivity: str
    audit: Optional[AuditLog] = None
    inbox: Optional[Inbox] = None


def create_app(storage: Optional[StorageBackend] = None, settings=None,
               processor_factory=None, identity=None) -> Flask:
    """``identity`` (request -> email or None) replaces the AUTH_MODE check; used by tests."""
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
    app.config["SECRET_KEY"] = settings.secret_key

    base_storage = storage or create_storage()
    sensitivity_choices = [{"key": k, "label": SENSITIVITY_LABELS[k]} for k in SENSITIVITY_ORDER]
    directory = OrgDirectory.load(settings.orgs_file) if settings.orgs_file else None
    if directory:
        if identity is None:
            identity = build_identity(settings.auth_mode, settings.cf_team_domain, settings.cf_aud,
                                      settings.dev_user_email)
        if settings.auth_mode != "dev" and settings.secret_key in ("", "change-me"):
            raise ValueError("Set a long random SECRET_KEY in .env before using several groups")
        app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                          SESSION_COOKIE_SECURE=(settings.auth_mode == "cloudflare"))

    waker = Waker(settings.wake_mac, settings.nas_host, settings.nas_port, settings.wake_broadcast,
                  settings.nas_marker)
    app.extensions["waker"] = waker
    contexts = {}

    def build_context(org: Optional[Org]) -> Context:
        content = (org.content_file if org else None) or settings.report_content_file or None
        rep = HeatLossReporter(content_file=content)
        st = PrefixedStorage(base_storage, org.slug) if org else base_storage
        sens = (org.sensitivity if org else None) or settings.sensitivity
        pal = (org.palette if org else None) or settings.palette
        svc = BatchService(st, rep.spot_types, sensitivity=sens, max_spots=settings.max_spots,
                           processor_factory=processor_factory, palette=pal, reporter=rep)
        box = (Inbox(settings.inbox_root, org.slug if org else "default", marker=settings.nas_marker,
                     waker=waker, max_files=settings.inbox_max_files) if settings.inbox_root else None)
        return Context(slug=org.slug if org else "", org_name=org.name if org else settings.org_name,
                       storage=st, service=svc, reporter=rep, sensitivity=svc.sensitivity,
                       audit=AuditLog(st, org.slug) if org else None, inbox=box)

    def context_for(slug: str) -> Context:
        if slug not in contexts:
            contexts[slug] = build_context(directory.orgs[slug])
        return contexts[slug]

    app.extensions["storage"] = base_storage
    app.extensions["context_for"] = context_for
    if directory is None:
        single = build_context(None)
        app.extensions["service"] = single.service
    service = LocalProxy(lambda: g.ctx.service)
    reporter = LocalProxy(lambda: g.ctx.reporter)
    storage = LocalProxy(lambda: g.ctx.storage)
    logger.info("Storage backend: %s%s", base_storage.describe(),
                f" ({len(directory.orgs)} groups)" if directory else "")

    # ---------------------------------------------------------------- who is this, and for which group?
    @app.before_request
    def resolve_context():
        g.ctx, g.user, g.orgs = None, "", []
        if directory is None:
            g.ctx = single
            return
        if request.endpoint in PUBLIC_ENDPOINTS:
            return
        email = identity(request)
        if not email:
            return Response("Sign-in required. Please open this site through its normal address.", 401)
        g.user, g.orgs = email, directory.for_user(email)
        if not g.orgs:
            logger.warning("Refused %s: not a member of any group", email)
            return render_template("no_access.html", email=email), 403
        slugs = [o.slug for o in g.orgs]
        chosen = session.get("org")
        if chosen not in slugs:
            session.pop("org", None)
            chosen = slugs[0] if len(slugs) == 1 else None
        if chosen is None:                                  # a member of several groups must choose
            if request.endpoint in ("choose_group", "switch_group"):
                return
            return redirect(url_for("choose_group"))
        g.ctx = context_for(chosen)

    @app.context_processor
    def test_banner():
        return {"test_banner": settings.banner_text}

    @app.context_processor
    def banner():
        ctx = getattr(g, "ctx", None)
        if directory is None or ctx is None:
            return {"org_banner": None}
        org = directory.orgs[ctx.slug]
        return {"org_banner": {"name": org.name, "colour": org.colour, "text": org.text_colour,
                               "user": g.user, "current": ctx.slug,
                               "orgs": [{"slug": o.slug, "name": o.name} for o in g.orgs] if len(g.orgs) > 1 else [],
                               "is_admin": directory.is_admin(g.user)}}

    def audit(action: str, batch_id: Optional[str] = None, **detail):
        if g.ctx is not None and g.ctx.audit is not None:
            g.ctx.audit.record(g.user, action, batch_id,
                               ip=request.headers.get("Cf-Connecting-Ip") or request.remote_addr or "", **detail)

    # ---------------------------------------------------------------- helpers
    def batch_or_404(batch_id: str) -> str:
        try:
            valid_batch_id(batch_id)
            if not service.batch_exists(batch_id):
                abort(404, "Batch not found")
        except BatchError:
            abort(404, "Batch not found")
        return batch_id

    def serve_bytes(path: str, filename: str, as_attachment: bool = False):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in REPORT_FILE_TYPES:
            abort(404)
        try:
            data = storage.read_bytes(path)
        except StorageNotFound:
            abort(404)
        return send_file(io.BytesIO(data), mimetype=REPORT_FILE_TYPES[ext],
                         as_attachment=as_attachment, download_name=filename)

    @app.errorhandler(BatchError)
    def _batch_error(exc):
        return jsonify({"error": str(exc)}), 400

    @app.errorhandler(StorageError)
    def _storage_error(exc):
        logger.error("Storage error: %s", exc)
        return jsonify({"error": f"Storage problem: {exc}"}), 503

    # ---------------------------------------------------------------- pages
    @app.route("/")
    def index():
        return render_template("index.html", batches=service.list_batches(),
                               batch_size=settings.batch_size, sensitivities=sensitivity_choices,
                               default_sensitivity=g.ctx.sensitivity, drive_enabled=g.ctx.inbox is not None)

    @app.route("/choose_group")
    def choose_group():
        if directory is None:
            return redirect(url_for("index"))
        return render_template("choose_group.html", orgs=g.orgs, user=g.user,
                               current=session.get("org"))

    @app.route("/switch_group", methods=["POST"])
    def switch_group():
        if directory is None:
            return redirect(url_for("index"))
        slug = request.form.get("org", "")
        if slug not in [o.slug for o in g.orgs]:
            abort(403)
        session["org"] = slug
        ctx = context_for(slug)
        ctx.audit.record(g.user, "switch_group", ip=request.headers.get("Cf-Connecting-Ip") or request.remote_addr or "")
        return redirect(url_for("index"))

    @app.route("/activity")
    def activity():
        """Who did what for this group. Admins only."""
        if directory is None or not directory.is_admin(g.user):
            abort(403)
        return render_template("activity.html", events=g.ctx.audit.recent(300), org_name=g.ctx.org_name)

    @app.route("/info")
    def info():
        return render_template("info.html")

    @app.route("/help")
    def help_page():
        return redirect(url_for("info"))

    @app.route("/healthz")
    def healthz():
        if directory is not None:                      # reachable without sign-in, so say as little as possible
            return jsonify({"status": "ok"})
        return jsonify({"status": "ok", "storage": g.ctx.storage.describe()})

    @app.route("/storage_status")
    def storage_status():
        """Is the NAS awake? (Always up/unconfigured unless WAKE_MAC and NAS_HOST are set.)"""
        return jsonify(waker.status())

    @app.route("/wake_storage", methods=["POST"])
    def wake_storage():
        return jsonify(waker.wake())

    # ---------------------------------------------------------------- upload / batches
    @app.route("/upload", methods=["POST"])
    def upload_files():
        drive_names = [n for n in request.form.getlist("drive_files") if n]
        files = [f for f in request.files.getlist("files[]") if f and f.filename]
        if drive_names and files:
            return jsonify({"error": "Choose pictures from your computer or from the drive, not both"}), 400
        if drive_names:                                  # pictures already parked on the drive
            box = g.ctx.inbox
            if box is None or not box.available():
                return jsonify({"error": "The drive is not available right now"}), 503
            if len(drive_names) > settings.batch_size:
                return jsonify({"error": f"Maximum {settings.batch_size} images per batch"}), 400
            try:
                payload = [(n, box.read(n)) for n in drive_names]
            except InboxError as exc:
                return jsonify({"error": str(exc)}), 400
            source = "drive"
        else:
            valid = [f for f in files
                     if os.path.splitext(secure_filename(f.filename))[1].lower() in (".jpg", ".jpeg")]
            if not valid:
                return jsonify({"error": "No valid FLIR images provided"}), 400
            if len(valid) > settings.batch_size:
                return jsonify({"error": f"Maximum {settings.batch_size} images per batch"}), 400
            payload, source = [(f.filename, f.read()) for f in valid], "computer"

        batch_id = new_batch_id([n for n, _ in payload])
        results = service.process_batch(batch_id, payload, sensitivity=request.form.get("sensitivity"))
        if not results.get("summary", {}).get("total_images"):
            service.delete_batch(batch_id)
            errors = "; ".join(i.get("error", "") for i in results["images"] if i.get("error"))
            return jsonify({"error": f"No image could be processed. {errors}".strip()}), 422
        if source == "drive" and settings.inbox_delete_after:
            for n, _ in payload:                          # the batch holds its own copy now
                try:
                    g.ctx.inbox.delete(n)
                except (InboxError, OSError):
                    pass
        audit("upload", batch_id, images=results["summary"]["total_images"], source=source)
        return jsonify({"success": True, "batch_id": batch_id, "results": results}), 201

    # ---------------------------------------------------------------- the drive (staging folder)
    def drive_or_error():
        """The group's drive folder, or (None, error response) when off or not reachable."""
        box = g.ctx.inbox
        if box is None:
            return None, (jsonify({"error": "No drive is set up"}), 404)
        if not box.available():
            return None, (jsonify({"error": "The drive is not available right now", "asleep": True}), 503)
        return box, None

    @app.route("/drive/status")
    def drive_status():
        box = g.ctx.inbox
        if box is None:
            return jsonify({"enabled": False, "available": False, "wake_configured": False})
        return jsonify({"enabled": True, "available": box.available(), "wake_configured": waker.configured})

    @app.route("/drive/list")
    def drive_list():
        box, err = drive_or_error()
        return err if err else jsonify({"files": box.list()})

    @app.route("/drive/thumb/<name>")
    def drive_thumb(name):
        box, err = drive_or_error()
        if err:
            return err
        try:
            data = box.thumb(name)
        except (InboxError, OSError):
            abort(404)
        resp = Response(data, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    @app.route("/drive/upload", methods=["POST"])
    def drive_upload():
        box, err = drive_or_error()
        if err:
            return err
        files = [f for f in request.files.getlist("files[]") if f and f.filename]
        try:
            stamps = json.loads(request.form.get("modified", "[]"))
        except ValueError:
            stamps = []
        saved, rejected = [], []
        for i, f in enumerate(files):
            try:
                ms = stamps[i] if i < len(stamps) else None
                saved.append(box.save(f.filename, f.read(), float(ms) / 1000.0 if ms else None))
            except (InboxError, ValueError, TypeError, OSError) as exc:
                rejected.append({"name": f.filename, "reason": str(exc)})
        if saved:
            audit("drive_upload", files=len(saved))
        return jsonify({"saved": saved, "rejected": rejected}), (201 if saved else 400)

    @app.route("/drive/delete", methods=["POST"])
    def drive_delete():
        box, err = drive_or_error()
        if err:
            return err
        names = (request.get_json(silent=True) or {}).get("names") or []
        removed = []
        for n in names:
            try:
                if box.delete(n):
                    removed.append(n)
            except (InboxError, OSError):
                continue
        if removed:
            audit("drive_delete", files=len(removed))
        return jsonify({"deleted": removed})

    @app.route("/report/<batch_id>")
    def view_report(batch_id):
        batch_or_404(batch_id)
        return render_template("report.html", batch_id=batch_id, report=service.get_results(batch_id))

    @app.route("/api/batches")
    def api_batches():
        return jsonify({"batches": service.list_batches()})

    @app.route("/api/report/<batch_id>")
    def api_report(batch_id):
        batch_or_404(batch_id)
        return jsonify(service.get_results(batch_id))

    @app.route("/delete/<batch_id>", methods=["POST", "DELETE"])
    def delete_batch(batch_id):
        batch_or_404(batch_id)
        service.delete_batch(batch_id)
        audit("delete_batch", batch_id)
        return jsonify({"success": True})

    # ---------------------------------------------------------------- files
    @app.route("/download/<batch_id>/<filename>")
    def download_file(batch_id, filename):
        batch_or_404(batch_id)
        name = secure_filename(filename)
        return serve_bytes(f"{service.rdir(batch_id)}/{name}", name,
                           as_attachment=name.endswith(".csv"))

    @app.route("/batch_image/<batch_id>/<filename>")
    def batch_image(batch_id, filename):
        """The original (unmarked) uploaded picture - the editor draws its own markers on it."""
        batch_or_404(batch_id)
        name = secure_filename(filename)
        return serve_bytes(f"{service.idir(batch_id)}/{name}", name)

    # ---------------------------------------------------------------- point definition + notes
    @app.route("/edit_spots/<batch_id>")
    def edit_spots(batch_id):
        batch_or_404(batch_id)
        return render_template("edit_spots.html", batch_id=batch_id,
                               state=service.build_edit_state(batch_id),
                               spot_types=reporter.spot_types, abbr=SPOT_ABBR,
                               options=reporter.options_by_type(), severities=severity_legend(),
                               sensitivities=sensitivity_choices)

    @app.route("/label_hotspots/<batch_id>")
    def label_hotspots_legacy(batch_id):
        return redirect(url_for("edit_spots", batch_id=batch_id))

    @app.route("/api/temperature/<batch_id>/<image_name>")
    def api_temperature(batch_id, image_name):
        batch_or_404(batch_id)
        try:
            row, col = int(request.args["row"]), int(request.args["col"])
        except (KeyError, ValueError):
            return jsonify({"error": "row and col are required integers"}), 400
        return jsonify(service.probe(batch_id, secure_filename(image_name), row, col))

    @app.route("/api/point_text", methods=["POST"])
    def api_point_text():
        """The sentence the report will use for a point, so the editor can show it live."""
        body = request.get_json(silent=True) or {}
        stype = body.get("type") if body.get("type") in reporter.spot_types else ""
        return jsonify({"comment": reporter.auto_comment(stype, body.get("temperature"),
                                                         body.get("excess"), body.get("severity", "low"))})

    @app.route("/redetect/<batch_id>", methods=["POST"])
    def redetect(batch_id):
        batch_or_404(batch_id)
        body = request.get_json(silent=True) or {}
        out = service.redetect(batch_id, str(body.get("sensitivity", "")))
        audit("redetect", batch_id, sensitivity=out["sensitivity"], points=out["points"])
        return jsonify({"success": True, **out})

    @app.route("/save_labels/<batch_id>", methods=["POST"])
    def save_labels(batch_id):
        batch_or_404(batch_id)
        summary = service.save_labels(batch_id, request.get_json(silent=True))
        audit("save_labels", batch_id, points=summary.get("labeled_spots"))
        return jsonify({"success": True, "message": "Saved", **summary})

    # ---------------------------------------------------------------- report
    @app.route("/generate_heat_loss_report/<batch_id>", methods=["POST"])
    def generate_heat_loss_report(batch_id):
        batch_or_404(batch_id)
        labels = service.get_labels(batch_id)
        if not reporter.labelled_spots(labels.get("spots", [])):
            return jsonify({"error": "Define at least one point (type and number) and save first."}), 400
        body = request.get_json(silent=True) or request.form
        report = reporter.build_report(
            batch_id, labels,
            property_address=(body.get("property_address") or "").strip(),
            inspector_name=(body.get("inspector_name") or "").strip(),
            images_meta=service.report_images(batch_id), org_name=g.ctx.org_name)
        report["generated_by"], report["group"] = g.user, g.ctx.slug
        service.save_report(batch_id, report)
        audit("generate_report", batch_id, property=report["property_address"])
        return jsonify({"success": True, "message": "Heat loss report generated successfully",
                        "report_url": url_for("view_heat_loss_report", batch_id=batch_id)})

    @app.route("/view_heat_loss_report/<batch_id>")
    def view_heat_loss_report(batch_id):
        batch_or_404(batch_id)
        try:
            report = service.get_report(batch_id)
        except StorageNotFound:
            return "Report not found. Please generate it first.", 404
        if "images" not in report:                      # saved by an older version of the app
            return ("This report was made by an older version. Open the points page and choose "
                    "Save &amp; Generate Report again.", 409)
        return render_template("heat_loss_report.html", report_data=report)

    return app


if __name__ == "__main__":
    cfg = load_settings()
    create_app(settings=cfg).run(debug=cfg.debug, host=cfg.host, port=cfg.port)
