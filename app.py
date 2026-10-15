#!/usr/bin/env python3
"""Thermal Report web application (merged build).

Flow: upload FLIR images -> hot spots detected -> surveyor defines points (type + number),
adds extra points by clicking, and writes freehand notes -> heat loss report is generated
with both the auto-generated comments and the surveyor's notes.

All files go through the storage backend chosen with STORAGE_BACKEND
(local | webdav | smb), so the app runs unchanged on any server.
"""
from __future__ import annotations

import logging
import os
from pathlib import PurePosixPath
from typing import Optional

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, url_for, Response)
from werkzeug.utils import secure_filename
import io

from heat_loss_reporter import HeatLossReporter
from services import BatchError, BatchService, new_batch_id, valid_batch_id
from settings import load_settings
from storage import StorageBackend, StorageError, StorageNotFound, create_storage

logger = logging.getLogger("thermal_report")

REPORT_FILE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".csv": "text/csv",
                     ".html": "text/html"}


def create_app(storage: Optional[StorageBackend] = None, settings=None,
               processor_factory=None) -> Flask:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
    app.config["SECRET_KEY"] = settings.secret_key

    storage = storage or create_storage()
    reporter = HeatLossReporter()
    service = BatchService(storage, reporter.spot_types, sensitivity=settings.sensitivity,
                           max_spots=settings.max_spots, processor_factory=processor_factory)
    app.extensions["storage"], app.extensions["service"] = storage, service
    logger.info("Storage backend: %s", storage.describe())

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
                               batch_size=settings.batch_size)

    @app.route("/info")
    def info():
        return render_template("info.html")

    @app.route("/help")
    def help_page():
        return redirect(url_for("info"))

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok", "storage": storage.describe()})

    # ---------------------------------------------------------------- upload / batches
    @app.route("/upload", methods=["POST"])
    def upload_files():
        files = [f for f in request.files.getlist("files[]") if f and f.filename]
        valid = [f for f in files
                 if os.path.splitext(secure_filename(f.filename))[1].lower() in (".jpg", ".jpeg")]
        if not valid:
            return jsonify({"error": "No valid FLIR images provided"}), 400
        if len(valid) > settings.batch_size:
            return jsonify({"error": f"Maximum {settings.batch_size} images per batch"}), 400

        batch_id = new_batch_id([f.filename for f in valid])
        results = service.process_batch(batch_id, [(f.filename, f.read()) for f in valid])
        if not results.get("summary", {}).get("total_images"):
            service.delete_batch(batch_id)
            errors = "; ".join(i.get("error", "") for i in results["images"] if i.get("error"))
            return jsonify({"error": f"No image could be processed. {errors}".strip()}), 422
        return jsonify({"success": True, "batch_id": batch_id, "results": results}), 201

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
                               spot_types=reporter.spot_types)

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

    @app.route("/save_labels/<batch_id>", methods=["POST"])
    def save_labels(batch_id):
        batch_or_404(batch_id)
        summary = service.save_labels(batch_id, request.get_json(silent=True))
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
            inspector_name=(body.get("inspector_name") or "").strip())
        service.save_report(batch_id, report)
        return jsonify({"success": True, "message": "Heat loss report generated successfully",
                        "report_url": url_for("view_heat_loss_report", batch_id=batch_id)})

    @app.route("/view_heat_loss_report/<batch_id>")
    def view_heat_loss_report(batch_id):
        batch_or_404(batch_id)
        try:
            report = service.get_report(batch_id)
        except StorageNotFound:
            return "Report not found. Please generate it first.", 404
        return render_template("heat_loss_report.html", report_data=report)

    return app


if __name__ == "__main__":
    cfg = load_settings()
    create_app(settings=cfg).run(debug=cfg.debug, host=cfg.host, port=cfg.port)
