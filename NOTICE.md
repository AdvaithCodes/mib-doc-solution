# Third-Party Components

All runtime dependencies are pinned in `requirements.txt` and vendored into the
image at build time. Nothing is fetched at runtime.

| Component | Version | License | Use |
| --- | --- | --- | --- |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | 5.12.1 | Apache-2.0 / BSD-3-Clause | PDF page rendering |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | 0.11.10 | MIT | text-layer geometry and character attributes |
| [pypdf](https://github.com/py-pdf/pypdf) | 6.14.2 | BSD-3-Clause | PDF structure inspection |
| [RapidOCR (ONNXRuntime)](https://github.com/RapidAI/RapidOCR) | 1.4.4 | Apache-2.0 | offline OCR |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 1.28.0 | MIT | OCR model inference |
| [OpenCV (headless)](https://github.com/opencv/opencv-python) | 5.0.0.93 | Apache-2.0 | image preprocessing |
| [Pillow](https://github.com/python-pillow/Pillow) | 12.3.0 | MIT-CMU | image I/O |
| [NumPy](https://github.com/numpy/numpy) | 2.5.1 | BSD-3-Clause | array operations |

## Model artifacts

RapidOCR ships PP-OCRv4 ONNX models, released by PaddlePaddle under Apache-2.0
and redistributed by RapidOCR under the same terms:

| Artifact | Size |
| --- | ---: |
| `ch_PP-OCRv4_det_infer.onnx` | 4.5 MiB |
| `ch_PP-OCRv4_rec_infer.onnx` | 10.4 MiB |
| `ch_ppocr_mobile_v2.0_cls_infer.onnx` | 0.6 MiB |
| **Total** | **15.4 MiB** |

Well inside the challenge limits of 250 MiB per artifact and 1 GiB total.
