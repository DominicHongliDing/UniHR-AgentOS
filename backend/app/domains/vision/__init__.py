"""Structured image and scanned-document evidence extraction."""

from .contracts import ImageEvidenceBlock, ImageEvidencePage, ImageEvidenceResult
from .service import VisionEvidenceExtractor
from .evaluation import VisionEvaluationCase, VisionEvaluationReport, VisionEvidenceEvaluator

__all__ = [
    "ImageEvidenceBlock",
    "ImageEvidencePage",
    "ImageEvidenceResult",
    "VisionEvidenceExtractor",
    "VisionEvaluationCase",
    "VisionEvaluationReport",
    "VisionEvidenceEvaluator",
]
