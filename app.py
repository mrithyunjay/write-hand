import os
import uuid
import subprocess
from pathlib import Path
from flask import (
    Flask, render_template, send_from_directory,
    request, redirect, url_for, flash, abort
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.urandom(24)

BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
TEMPLATE_PNG = BASE_DIR / "template.jpg"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def sanitize_text(value: str) -> str:
    """Keep only safe characters for --family / --style arguments."""
    return "".join(c for c in value if c.isalnum() or c in (" ", "-", "_")).strip()


# ── Page 1: download the template PNG ────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download-template")
def download_template():
    if not TEMPLATE_PNG.exists():
        abort(404, description="template.jpg not found next to app.py")
    return send_from_directory(BASE_DIR, "template.jpg", as_attachment=True)


# ── Page 2: upload PNG + text inputs, run handwrite ──────────────────────────

@app.route("/generate", methods=["GET", "POST"])
def generate():
    if request.method == "GET":
        return render_template("generate.html")

    # --- validate file ---
    if "pngfile" not in request.files:
        flash("No file part in the request.")
        return redirect(url_for("generate"))

    file = request.files["pngfile"]
    if file.filename == "":
        flash("No file selected.")
        return redirect(url_for("generate"))

    if not allowed_file(file.filename):
        flash("Only JPG or PNG files are accepted.")
        return redirect(url_for("generate"))

    # --- validate text inputs ---
    family   = sanitize_text(request.form.get("family", ""))
    style    = sanitize_text(request.form.get("style", ""))
    filename = sanitize_text(request.form.get("filename", ""))

    if not family:
        flash("Font family name is required.")
        return redirect(url_for("generate"))
    if not style:
        flash("Font style is required.")
        return redirect(url_for("generate"))
    if not filename:
        flash("File name is required.")
        return redirect(url_for("generate"))

    # --- save upload with a unique name, preserving extension ---
    job_id   = uuid.uuid4().hex
    ext      = file.filename.rsplit(".", 1)[1].lower()
    img_path = UPLOAD_DIR / f"{job_id}.{ext}"

    file.save(img_path)

    # --- run handwrite as a subprocess (no shell=True) ---
    # handwrite treats the second argument as an output DIRECTORY,
    # and names the file using --filename inside it.
    cmd = [
        "handwrite",
        str(img_path),
        str(OUTPUT_DIR),
        "--family",   family,
        "--style",    style,
        "--filename", filename,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2-minute hard limit
        )
    except FileNotFoundError:
        img_path.unlink(missing_ok=True)
        flash("handwrite is not installed. Run: pip install handwrite")
        return redirect(url_for("generate"))
    except subprocess.TimeoutExpired:
        img_path.unlink(missing_ok=True)
        flash("Font generation timed out. Please try again.")
        return redirect(url_for("generate"))

    # --- clean up the upload regardless of outcome ---
    img_path.unlink(missing_ok=True)

    if result.returncode != 0:
        flash(f"handwrite failed: {result.stderr.strip() or result.stdout.strip()}")
        return redirect(url_for("generate"))

    # handwrite writes <filename>.ttf into OUTPUT_DIR
    ttf_path = OUTPUT_DIR / f"{filename}.ttf"
    if not ttf_path.exists():
        flash("handwrite ran but produced no output file.")
        return redirect(url_for("generate"))

    return redirect(url_for("download_font", job_id=filename))

    return redirect(url_for("download_font", job_id=job_id))


# ── Page 3: download the generated TTF ───────────────────────────────────────

@app.route("/font/<job_id>")
def download_font(job_id):
    # job_id is the user-supplied filename, already sanitized at upload time
    safe = sanitize_text(job_id)
    if not safe or safe != job_id:
        abort(400)

    ttf_path = OUTPUT_DIR / f"{job_id}.ttf"
    if not ttf_path.exists():
        abort(404, description="Font not found. It may have already been downloaded.")

    return render_template("download.html", job_id=job_id)


@app.route("/font/<job_id>/file")
def serve_font_file(job_id):
    safe = sanitize_text(job_id)
    if not safe or safe != job_id:
        abort(400)

    ttf_path = OUTPUT_DIR / f"{job_id}.ttf"
    if not ttf_path.exists():
        abort(404)

    response = send_from_directory(OUTPUT_DIR, f"{job_id}.ttf", as_attachment=True,
                                   download_name=f"{job_id}.ttf")

    @response.call_on_close
    def cleanup():
        ttf_path.unlink(missing_ok=True)

    return response


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)