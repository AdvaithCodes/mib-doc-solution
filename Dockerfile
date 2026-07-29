# Offline submission image for the 8090 MIB Doc Challenge.
#
# Contract: the image takes exactly two arguments,
#   <input_pdf_dir> <output_predictions_path>
# and is scored with --network none --cpus 4 --memory 8g --read-only
# with a 2 GiB tmpfs on /tmp. Nothing outside /tmp and /output is writable.

FROM python:3.12-slim

WORKDIR /app

# Tesseract is the second OCR engine. RapidOCR (PP-OCR detection plus
# recognition) and Tesseract (classical LSTM line recognition) fail in
# uncorrelated ways, which is what makes a second opinion worth anything:
# rendering the same page at 150 and 200 dpi was measured and gained nothing,
# because two passes of one engine make the same mistakes.
#
# Installed at build time only. The container still needs no network to run.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

# PDF rendering, image processing and RapidOCR all ship as wheels.
COPY requirements.txt /app/requirements.txt
# rapidocr-onnxruntime depends on opencv-python, which needs libGL that slim
# lacks. Removing it also deletes the shared cv2/ directory that
# opencv-python-headless installed, so headless has to be reinstalled after the
# uninstall -- otherwise `import cv2` fails at runtime even though pip lists the
# package as present.
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps \
      opencv-python-headless==5.0.0.93 \
 && find /usr/local -name "__pycache__" -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

COPY run.sh /app/run.sh
COPY mib_pipeline /app/mib_pipeline
RUN chmod +x /app/run.sh

# Fail the build if the runtime cannot import, rather than at scoring time.
RUN python -c "import cv2, numpy, pypdfium2, pdfplumber, rapidocr_onnxruntime; print('imports ok')"

# Both OCR engines must be present. A missing tesseract degrades silently to
# single-engine reads, which is exactly the failure that would be invisible
# until scoring.
RUN tesseract --version && python -c "\
from mib_pipeline import ocr_tesseract; \
assert ocr_tesseract.available(), 'tesseract not on PATH'; \
print('both OCR engines present')"

# Keep OCR inside the 4 vCPU budget and avoid thread oversubscription.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

ENTRYPOINT ["/app/run.sh"]
