"""The check that every office and venue card reads as code's plain sentences
(scripts/check_plain_writer.py)."""

from pathlib import Path

import check_plain_writer
import pytest

from rockygpt_brain.retrieval.graph_store import GraphOnlyFacts, publish_release_graph
from test_graph_store import NOW, basic


def test_every_card_of_a_release_graph_is_looked_up_the_ways_a_student_asks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publish_release_graph(basic(), tmp_path)
    facts = GraphOnlyFacts(tmp_path, now=lambda: NOW)
    assert check_plain_writer.unsayable(facts, NOW) == []
    # A card the plain writer can't say is listed with the lookup that made it.
    monkeypatch.setattr(check_plain_writer, "plain_text", lambda view: None)
    found = check_plain_writer.unsayable(facts, NOW)
    assert found and {row["why"] for row in found} == {"the plain writer can't say it"}
    assert {tuple(row["fields"]) for row in found} <= {
        fields for fields, _ in check_plain_writer.SHAPES}
