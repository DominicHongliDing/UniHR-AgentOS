from __future__ import annotations

import json

from app.domains.knowledge import KnowledgeVectorIndex, PolicyKnowledgeRetriever
from app.domains.knowledge.evaluation import PolicyRetrievalCase, PolicyRetrievalEvaluator
from app.models.embeddings import BailianEmbeddingGateway
from app.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    assert settings.database_path is not None
    dataset_path = settings.data_dir / "policy-retrieval-golden.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = [
        PolicyRetrievalCase(
            case_id=str(item["case_id"]),
            question=str(item["question"]),
            expected_sources=tuple(str(value) for value in item["expected_sources"]),
            query_variants=tuple(str(value) for value in item.get("query_variants", [])),
            expected_no_answer=bool(item.get("expected_no_answer", False)),
        )
        for item in payload
    ]
    retriever = PolicyKnowledgeRetriever(
        KnowledgeVectorIndex(settings.database_path, BailianEmbeddingGateway(settings))
    )
    report = PolicyRetrievalEvaluator(retriever).evaluate(cases, top_k=10)
    print(json.dumps({
        "case_count": report.case_count,
        "top_k": report.top_k,
        "hit_rate": round(report.hit_rate, 4),
        "recall_at_k": round(report.recall_at_k, 4),
        "mean_reciprocal_rank": round(report.mean_reciprocal_rank, 4),
        "no_answer_accuracy": round(report.no_answer_accuracy, 4),
        "cases": [
            {
                "case_id": item.case_id,
                "hit": item.hit,
                "reciprocal_rank": round(item.reciprocal_rank, 4),
                "matched_sources": item.matched_sources,
                "retrieved_sources": item.retrieved_sources,
            }
            for item in report.results
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
