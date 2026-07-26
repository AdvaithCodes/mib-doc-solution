# Offline submission image for the 8090 MIB Doc Challenge.
#
# Contract: the image takes exactly two arguments,
#   <input_pdf_dir> <output_predictions_path>
# and is scored with --network none --cpus 4 --memory 8g --read-only
# with a 2 GiB tmpfs on /tmp. Nothing outside /tmp and /output is writable.

FROM python:3.12-slim

WORKDIR /app

# No apt packages: PDF rendering, image processing and OCR all ship as wheels.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && pip uninstall -y opencv-python \
 && find /usr/local -name "__pycache__" -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

COPY run.sh /app/run.sh
COPY mib_pipeline /app/mib_pipeline
RUN chmod +x /app/run.sh

# Fail the build if the runtime cannot import, rather than at scoring time.
RUN python -c "import cv2, numpy, pypdfium2, pdfplumber, rapidocr_onnxruntime; print('imports ok')"

# Keep OCR inside the 4 vCPU budget and avoid thread oversubscription.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

ENTRYPOINT ["/app/run.sh"]
