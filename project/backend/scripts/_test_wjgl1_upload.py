"""Quick smoke test for WJGL1 upload parse endpoints."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
from flask import Flask

from blueprints.ZSGL.WJGL1.routes import wjgl1_bp

app = Flask(__name__)
app.register_blueprint(wjgl1_bp)
client = app.test_client()

fd, path = tempfile.mkstemp(suffix=".pdf")
os.close(fd)
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 72), "1 范围\n本标准规定了试验方法。")
doc.save(path)
doc.close()

try:
    with open(path, "rb") as f:
        r = client.post(
            "/api/wjgl1/auto-parse",
            data={"file": (f, "test.pdf")},
            content_type="multipart/form-data",
        )
    print("auto-parse", r.status_code, r.get_json())

    with open(path, "rb") as f:
        r2 = client.post(
            "/api/wjgl1/document-full-content-upload?format=html",
            data={"file": (f, "test.pdf")},
            content_type="multipart/form-data",
        )
    print(
        "full-content",
        r2.status_code,
        "html_bytes=",
        len(r2.data or b""),
        "mineru_key=",
        r2.headers.get("X-WJGL-MinerU-Key"),
    )
    if r2.status_code >= 400:
        print("full-content body:", r2.get_data(as_text=True)[:500])

    with open(path, "rb") as f:
        r3 = client.post(
            "/api/wjgl1/document-toc-upload",
            data={"file": (f, "test.pdf")},
            content_type="multipart/form-data",
        )
    print("toc-upload", r3.status_code, r3.get_json())
finally:
    os.unlink(path)
