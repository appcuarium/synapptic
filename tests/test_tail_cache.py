"""The transcript reader keeps what it parsed on disk: a new reader of the same file resumes from the offset
read instead of parsing the whole transcript again, reads only what was appended since, and starts over when
the file was replaced or shrank. The cache lives under synapptic's own directory, never in a project."""

import json

import pytest

from synapptic.sessions import tail as tail_module
from synapptic.sessions.tail import TranscriptTail


def record(role, text, stamp):
    return json.dumps({"type": role, "sessionId": "s", "timestamp": stamp, "uuid": f"{role}-{stamp}",
                       "message": {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}],
                                   "model": "claude-sonnet-5-5"}})


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    # boundary: filesystem - the cache folder is this test's own, never ~/.synapptic
    folder = tmp_path / "cache"
    monkeypatch.setattr(tail_module, "TAIL_CACHE_DIR", folder)
    return folder


def test_a_new_reader_resumes_from_the_cache_and_reads_only_the_new_lines(tmp_path, cache_dir):
    path = tmp_path / "s.jsonl"
    path.write_text(record("user", "hello", "2026-11-04T10:00:00Z") + "\n" + record("assistant", "hi", "2026-11-04T10:00:01Z") + "\n")
    first = TranscriptTail(path, scrub=False)
    first.update()
    assert len(first.turns) == 2 and first.cache_path.exists() and first.cache_path.parent == cache_dir

    second = TranscriptTail(path, scrub=False)
    assert second.offset == first.offset and len(second.turns) == 2 and second.title == first.title
    assert second.update() is None                      # nothing new: nothing read

    with path.open("a") as out:
        out.write(record("user", "and more", "2026-11-04T10:00:02Z") + "\n")
    assert second.update() == 2 and len(second.turns) == 3 and second.message_count == 3


def test_a_replaced_or_shrunk_file_is_read_again_from_the_start(tmp_path, cache_dir):
    path = tmp_path / "s.jsonl"
    path.write_text(record("user", "hello", "2026-11-04T10:00:00Z") + "\n" + record("assistant", "hi", "2026-11-04T10:00:01Z") + "\n")
    TranscriptTail(path, scrub=False).update()
    path.write_text(record("user", "a fresh start", "2026-11-04T11:00:00Z") + "\n")
    reader = TranscriptTail(path, scrub=False)
    assert reader.update() == 0
    assert [turn["blocks"][0]["text"] for turn in reader.turns] == ["a fresh start"]
    assert reader.offset == path.stat().st_size


def test_the_scrubbed_and_raw_readers_keep_separate_caches(tmp_path, cache_dir):
    path = tmp_path / "s.jsonl"
    path.write_text(record("user", "hello", "2026-11-04T10:00:00Z") + "\n")
    assert TranscriptTail(path, scrub=True).cache_path != TranscriptTail(path, scrub=False).cache_path
