import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "ragent_locomo"
    / "run_locomo_deermem.py"
)
SPEC = importlib.util.spec_from_file_location("run_locomo_deermem", MODULE_PATH)
locomo = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(locomo)


def test_turn_message_preserves_evidence_metadata():
    message = locomo._turn_message(
        {
            "dia_id": "D1:2",
            "speaker": "Caroline",
            "text": "I joined a support group.",
        },
        session_name="session_1",
        date="2023-05-07",
        primary_speaker="Caroline",
    )
    assert message["role"] == "user"
    assert message["metadata"] == {
        "dia_id": "D1:2",
        "session": "session_1",
        "date": "2023-05-07",
        "speaker": "Caroline",
    }


def test_retrieve_uses_metadata_dia_id():
    class Provider:
        def search(self, query, top_k, thread_id):
            assert query == "support group"
            assert top_k == 5
            assert thread_id == "sample-1"
            return {
                "results": [{
                    "content": "Caroline joined a support group.",
                    "metadata": {
                        "dia_id": "D1:2",
                        "session": "session_1",
                        "date": "2023-05-07",
                        "speaker": "Caroline",
                    },
                }],
            }

    snippets, dia_ids, rows = locomo.retrieve(
        Provider(),
        "support group",
        sample_id="sample-1",
        top_k=5,
    )
    assert dia_ids == ["D1:2"]
    assert "dia_id=D1:2" in snippets[0]
    assert rows[0]["metadata"]["speaker"] == "Caroline"


def test_recall_at_k_matches_official_evidence_ids():
    assert locomo.recall_at_k(["D1:2"], ["D1:2", "D3:4"]) == 0.5
    assert locomo.recall_at_k(["D1:2"], []) is None
