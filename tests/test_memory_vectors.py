"""Synthetic embeddings; no paid provider requests."""

import math
from unittest.mock import Mock

import pytest
import requests

from services import memory_database as sql
from services import memory_session as sessions
from services import memory_vectors as vectors
from services import persistent_memory as memory


def vector(axis=0):
    return [1.0 if i == axis else 0.0 for i in range(vectors.DIMENSIONS)]


def session(tmp_path, name="Alice"):
    return sessions.MemorySession(name, model=Mock(), root=tmp_path)


def save(selected, text, kind="fact", title=""):
    return selected.store.apply(selected.key, {"operation": "save", "kind": kind, "title": title, "text": text}, {})[0]


def test_api_batch_order_normalization_and_deadline(monkeypatch):
    post = Mock()
    post.return_value.json.return_value = {"data": [
        {"index": 1, "embedding": vector(1)}, {"index": 0, "embedding": vector(0)}]}
    monkeypatch.setattr(vectors.requests, "post", post)
    model = vectors.Embeddings(lambda: "synthetic-key")
    assert model.embed(["one", "two"]) == [vector(0), vector(1)]
    assert post.call_args.args == ("https://api.mistral.ai/v1/embeddings",)
    assert post.call_args.kwargs["timeout"] <= 4
    assert post.call_args.kwargs["json"] == {"model": vectors.MODEL, "input": ["one", "two"]}
    assert len(vectors.pack(vector())) == 4096
    assert math.isclose(sum(x*x for x in vectors.unpack(vectors.pack(vector()))), 1)


@pytest.mark.parametrize("payload", [
    {"data": []}, {"data": [{"index": 0, "embedding": [1]}]},
    {"data": [{"index": 0, "embedding": [float("nan")] * 1024}]},
    {"data": [{"index": 2, "embedding": vector()}]},
])
def test_bad_api_output_fails_with_fixed_public_error(monkeypatch, payload):
    post = Mock()
    post.return_value.json.return_value = payload
    monkeypatch.setattr(vectors.requests, "post", post)
    with pytest.raises(memory.MemoryProblem, match="Vectorisation indisponible"):
        vectors.Embeddings(lambda: "PRIVATE-KEY").embed(["PRIVATE-TEXT"])


def test_reworded_recall_is_scoped_and_unrelated_vectors_are_omitted(tmp_path):
    alice, bob = session(tmp_path), session(tmp_path, "Bob")
    try:
        save(alice, "I develop numerical reactor simulation software.")
        save(alice, "My suitcase is blue.")
        save(bob, "PRIVATE OTHER PROFILE.")
        indexer = Mock()
        indexer.embed.return_value = [vector(0), vector(1), vector(0)]
        assert vectors.index_pending(alice.store, indexer) == 3
        alice.embeddings = Mock()
        alice.embeddings.embed.return_value = [vector(0)]
        result = alice.context("What career might suit me?")
        assert "reactor" in result and "suitcase" not in result and "PRIVATE" not in result
        assert len(result) <= memory.CONTEXT_LIMIT
        # Strongly dissimilar query: do not force the nearest memories into the answer.
        alice.embeddings.embed.return_value = [vector(2)]
        assert alice.context("Which sport?") == ""
    finally:
        alice.close()
        bob.close()


def test_timeout_uses_local_text_and_edits_invalidate_vectors(tmp_path):
    selected = session(tmp_path)
    try:
        item = save(selected, "Tools are in the garage.")
        indexer = Mock()
        indexer.embed.return_value = [vector()]
        vectors.index_pending(selected.store, indexer)
        selected.embeddings = Mock()
        selected.embeddings.embed.side_effect = memory.MemoryProblem("synthetic timeout")
        assert "garage" in selected.context("Where are the tools?")
        data = selected.store.read(selected.key)
        selected.store.apply(selected.key, {"operation": "replace", "ids": [item["id"]], "text": "Tools are in the attic."}, data)
        with sql.connection(selected.store) as db:
            assert db.execute("SELECT embedding FROM memory_items").fetchone()[0] is None
        selected.embeddings.embed.reset_mock()
        assert "attic" in selected.context("Where are the tools?")
        selected.embeddings.embed.assert_not_called()
    finally:
        selected.close()


def test_edits_during_index_and_forget_during_recall_discard_stale_data(tmp_path):
    selected = session(tmp_path)
    try:
        item = save(selected, "Old reactor location.")
        def edit(_):
            selected.store.apply(selected.key, {"operation": "replace", "ids": [item["id"]], "text": "New reactor location."}, selected.store.read(selected.key))
            return [vector()]
        indexer = Mock()
        indexer.embed.side_effect = edit
        assert vectors.index_pending(selected.store, indexer) == 0
        indexer.embed.side_effect = None
        indexer.embed.return_value = [vector()]
        vectors.index_pending(selected.store, indexer)
        def forget(_):
            selected.store.apply(selected.key, {"operation": "remove", "ids": [item["id"]]}, selected.store.read(selected.key))
            return [vector()]
        selected.embeddings = Mock()
        selected.embeddings.embed.side_effect = forget
        assert selected.context("Where is my workplace?") == ""
    finally:
        selected.close()


def test_provider_timeout_never_exposes_inputs(monkeypatch):
    monkeypatch.setattr(vectors.requests, "post", Mock(side_effect=requests.Timeout("PRIVATE")))
    with pytest.raises(memory.MemoryProblem) as error:
        vectors.Embeddings(lambda: "PRIVATE-KEY").embed(["PRIVATE-TEXT"])
    assert "PRIVATE" not in str(error.value)
