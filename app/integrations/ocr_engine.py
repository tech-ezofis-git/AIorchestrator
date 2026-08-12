"""Placeholder OCR engine client.

This is intentionally mocked. No real OCR engine/provider has been chosen
yet (Tesseract? a cloud OCR API? TBD) — auth mechanism, request shape, and
response schema are all unknown at the time this was written, so nothing
here should be treated as a real integration. `run_ocr` is a TODO that
returns realistic-shaped placeholder output (text + confidence + source
reference) so the OCR agent and the rest of the orchestrator can be built
and tested against a stable interface now.

When a real OCR engine is chosen, wire it in here — e.g.:
  - an httpx.AsyncClient (or the provider's SDK) configured with auth
  - proper error handling / retries for the actual engine's failure modes
  - request/response models matching the real engine's payloads
No other module should need to change; they only depend on this class's
method signature.
"""
import hashlib
from typing import Any

# References containing one of these (case-insensitive) deliberately score
# low confidence, so tests/demos can reliably exercise that path without
# depending on a specific hash outcome.
_LOW_CONFIDENCE_MARKERS = ("blurry", "low-quality", "low_quality", "scan-error")


def _confidence_for_reference(reference: str) -> float:
    """Deterministic, reference-derived confidence — the same reference
    always yields the same confidence (keeps tests reproducible), but
    different references vary, so the field reads as meaningful rather
    than a hardcoded constant. A real OCR engine's confidence genuinely
    varies with image quality; this mocks that without true randomness."""
    lowered = reference.lower()
    digest = hashlib.sha256(reference.encode()).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    if any(marker in lowered for marker in _LOW_CONFIDENCE_MARKERS):
        return round(0.30 + fraction * 0.25, 2)  # ~0.30-0.55
    return round(0.75 + fraction * 0.24, 2)  # ~0.75-0.99


class OcrEngineClient:
    def __init__(self):
        # TODO(ocr-engine): load real OCR engine base URL/credentials from
        # Settings once a provider is chosen (e.g. OCR_ENGINE_BASE_URL,
        # OCR_ENGINE_API_KEY).
        pass

    async def run_ocr(self, reference: str) -> dict[str, Any]:
        """TODO: replace with a real OCR engine call. Provider not yet
        chosen; do not treat this as real.

        Returns realistic-shaped placeholder output: extracted text, a
        confidence score, and the source reference — the same shape a
        real engine response would have, so swapping one in later doesn't
        require reshaping callers.
        """
        confidence = _confidence_for_reference(reference)
        return {
            "source_reference": reference,
            "text": (
                f"Placeholder OCR text extracted from '{reference}'. In a real "
                "integration this would be the actual text recognized in the "
                "referenced document/image."
            ),
            "confidence": confidence,
            "mock": True,
        }
