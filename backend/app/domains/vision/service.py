from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from app.domains.conversations.contracts import AttachmentRecord
from app.domains.runs import RunPayloadStore, RunPointer
from app.models.gateway import ModelGateway, ModelGatewayError, parse_json_object

from .contracts import ImageEvidenceBlock, ImageEvidencePage, ImageEvidenceResult


@dataclass(frozen=True)
class _EvidenceImage:
    source_name: str
    page: int
    mime_type: str
    content: bytes
    width: int
    height: int


class VisionEvidenceExtractor:
    """Convert pixels into reviewable evidence; never treat model confidence as OCR accuracy."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        model: str,
        localization_model: str | None = None,
        max_pages: int = 8,
        review_confidence: float = 0.82,
        payload_store: RunPayloadStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.localization_model = localization_model or model
        self.max_pages = max_pages
        self.review_confidence = review_confidence
        self.payload_store = payload_store

    def extract(
        self,
        attachments: Iterable[AttachmentRecord],
        *,
        payload_scope: RunPointer | None = None,
    ) -> ImageEvidenceResult:
        targets: list[_EvidenceImage] = []
        source_names: set[str] = set()
        truncated = False
        for attachment in attachments:
            remaining = self.max_pages - len(targets)
            if remaining <= 0:
                truncated = True
                break
            images = self._attachment_images(attachment, remaining)
            if images:
                source_names.add(attachment.display_name)
                targets.extend(images)
            expected = len(attachment.summary.get("low_text_pages") or [])
            if attachment.extension == ".pdf" and expected > len(images):
                truncated = True

        pages = [self._extract_page(target) for target in targets]
        extracted_text = "\n\n".join(
            f"[{page.source_name} 第 {page.page} 页]\n{page.page_text}" for page in pages if page.page_text
        )
        result = ImageEvidenceResult(
            pages=pages,
            extracted_text=extracted_text,
            source_count=len(source_names),
            page_count=len(pages),
            block_count=sum(len(page.blocks) for page in pages),
            review_block_count=sum(
                1 for page in pages for block in page.blocks if block.needs_review
            ),
            truncated=truncated,
        )
        if payload_scope is not None and self.payload_store is not None and pages:
            ref = self.payload_store.put_json(
                payload_scope,
                result.model_dump(mode="json", exclude={"payload_ref", "trace"}),
                kind="vision.evidence",
            )
            result.payload_ref = ref.model_dump(mode="json")
        result.trace = self._trace(result)
        return result

    def _attachment_images(self, attachment: AttachmentRecord, limit: int) -> list[_EvidenceImage]:
        if attachment.extension in {".png", ".jpg", ".jpeg", ".webp"}:
            content = Path(attachment.saved_path).read_bytes()
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
            return [_EvidenceImage(
                attachment.display_name, 1, attachment.mime_type or "image/png",
                content, width, height,
            )]
        if attachment.extension != ".pdf" or not attachment.summary.get("requires_vision"):
            return []
        low_pages = [int(value) for value in attachment.summary.get("low_text_pages") or []][:limit]
        if not low_pages:
            return []
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("pymupdf_not_installed") from exc
        document = fitz.open(attachment.saved_path)
        rendered: list[_EvidenceImage] = []
        try:
            for page_number in low_pages:
                if page_number < 1 or page_number > document.page_count:
                    continue
                pixmap = document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                rendered.append(_EvidenceImage(
                    attachment.display_name, page_number, "image/png", pixmap.tobytes("png"),
                    pixmap.width, pixmap.height,
                ))
        finally:
            document.close()
        return rendered

    def _extract_page(self, image: _EvidenceImage) -> ImageEvidencePage:
        prompt = (
            "读取这张人事业务材料图片，只输出 JSON 对象。不要猜测模糊内容。"
            "格式：{\"page_text\":\"按阅读顺序合并的全文\",\"blocks\":["
            "{\"text\":\"原文\",\"kind\":\"text|field|table|handwriting|stamp|other\","
            "\"bbox\":[x0,y0,x1,y1],\"confidence\":0到1}]}。"
            "bbox 必须使用 0 到 999 的归一化坐标；confidence 是模型自评，不是客观准确率。"
            "数字、日期、姓名、金额必须逐字保留；无法确认时保留 ? 并降低 confidence。"
        )
        data_url = f"data:{image.mime_type};base64,{base64.b64encode(image.content).decode('ascii')}"
        payload = self._complete_json(
            prompt, data_url, model=self.model,
            purpose="image_evidence_extraction", structured=False,
        )
        if payload is None:
            return ImageEvidencePage(
                source_name=image.source_name, page=image.page,
                width=image.width, height=image.height,
                needs_review=True, review_reasons=["ocr_model_unavailable"],
            )

        blocks = [self._normalize_block(raw) for raw in (payload.get("blocks") or []) if isinstance(raw, dict)]
        page_text = str(payload.get("page_text") or "").strip()
        if page_text and not blocks and self.localization_model != self.model:
            localization_prompt = (
                "请把下列已识别文字定位回图片，只输出 JSON 对象，不要改写文字：\n"
                f"{page_text[:12_000]}\n\n"
                "输出 {\"blocks\":[{\"text\":\"图片中的连续原文\","
                "\"kind\":\"text|field|table|handwriting|stamp|other\","
                "\"bbox\":[x0,y0,x1,y1],\"confidence\":0到1}]}。"
                "bbox 使用 0 到 999 的归一化坐标。每个有业务意义的行至少一个 block；"
                "包含 ? 的文字必须原样保留并降低 confidence。"
            )
            localized = self._complete_json(
                localization_prompt, data_url, model=self.localization_model,
                purpose="image_evidence_localization", structured=True,
            )
            if localized is not None:
                blocks = [
                    self._normalize_block(raw)
                    for raw in (localized.get("blocks") or [])
                    if isinstance(raw, dict)
                ]
        if not page_text:
            page_text = "\n".join(block.text for block in blocks if block.text).strip()
        page_reasons: list[str] = []
        if not page_text:
            page_reasons.append("no_text_extracted")
        elif not blocks:
            page_reasons.append("unlocalized_text")
        if any(block.needs_review for block in blocks):
            page_reasons.append("contains_review_blocks")
        return ImageEvidencePage(
            source_name=image.source_name, page=image.page,
            width=image.width, height=image.height, page_text=page_text, blocks=blocks,
            needs_review=bool(page_reasons), review_reasons=page_reasons,
        )

    def _complete_json(
        self,
        prompt: str,
        data_url: str,
        *,
        model: str,
        purpose: str,
        structured: bool,
    ) -> dict[str, Any] | None:
        try:
            reply = self.gateway.complete(
                [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]}],
                purpose=purpose,
                model=model,
                structured=structured,
                max_tokens=4_000,
            )
            return parse_json_object(reply.content)
        except (ModelGatewayError, ValueError, TypeError, KeyError):
            return None

    def _normalize_block(self, raw: dict[str, Any]) -> ImageEvidenceBlock:
        text = str(raw.get("text") or "").strip()
        kind = str(raw.get("kind") or "text").casefold()
        if kind not in {"text", "field", "table", "handwriting", "stamp", "other"}:
            kind = "other"
        invalid_bbox = False
        values = raw.get("bbox")
        if not isinstance(values, list) or len(values) != 4:
            bbox = [0, 0, 999, 999]
            invalid_bbox = True
        else:
            try:
                bbox = [max(0, min(999, int(round(float(value))))) for value in values]
                if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
                    invalid_bbox = True
                    bbox = [min(bbox[0], bbox[2]), min(bbox[1], bbox[3]), max(bbox[0], bbox[2]), max(bbox[1], bbox[3])]
            except (TypeError, ValueError):
                bbox = [0, 0, 999, 999]
                invalid_bbox = True
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        reasons: list[str] = []
        if confidence < self.review_confidence:
            reasons.append("low_model_confidence")
        if invalid_bbox:
            reasons.append("invalid_bbox")
        if "?" in text or "？" in text or "�" in text:
            reasons.append("uncertain_character")
        if re.search(r"\d", text) and confidence < max(self.review_confidence, 0.9):
            reasons.append("numeric_evidence_requires_review")
        return ImageEvidenceBlock(
            text=text, kind=kind, bbox=bbox, model_confidence=confidence,
            needs_review=bool(reasons), review_reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _trace(result: ImageEvidenceResult) -> list[dict[str, Any]]:
        review_page_count = sum(1 for page in result.pages if page.needs_review)
        trace = [{
            "event": "vision.evidence.extracted", "status": "completed",
            "summary": f"从 {result.source_count} 个来源提取 {result.block_count} 个可定位证据块",
            "details": {
                "page_count": result.page_count,
                "block_count": result.block_count,
                "review_block_count": result.review_block_count,
                "review_page_count": review_page_count,
                "truncated": result.truncated,
                "payload_ref": result.payload_ref,
            },
        }]
        if result.review_block_count or review_page_count:
            trace.append({
                "event": "vision.evidence.review_required", "status": "waiting",
                "summary": f"{result.review_block_count} 个证据块、{review_page_count} 页需要人工核对原图",
                "details": {
                    "review_block_count": result.review_block_count,
                    "review_page_count": review_page_count,
                },
            })
        return trace
