"""Behavioral checks of the private, incremental document vault."""

import os
from pathlib import Path

import pytest


def make_vault(tmp_path, **kwargs):
    from jarvis.vault import Vault

    root = tmp_path / "notes"
    root.mkdir(exist_ok=True)
    return Vault([root], tmp_path / "state" / "vault.sqlite", **kwargs), root


def test_indexes_and_returns_citable_bounded_text(tmp_path):
    vault, root = make_vault(tmp_path)
    note = root / "plan.md"
    note.write_text("Private roadmap " * 500)
    assert vault.index() == {"indexed": 1, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    hits = vault.search("roadmap", limit=2)
    assert len(hits) == 2
    assert all(hit["source"] and hit["chunk_id"] and hit["path"] == str(note) for hit in hits)
    assert all("roadmap" in hit["text"] and len(hit["text"]) <= 1500 for hit in hits)
    assert os.stat(vault.db_path).st_mode & 0o777 == 0o600


def test_hash_updates_and_deletion_reconcile_derived_evidence(tmp_path):
    vault, root = make_vault(tmp_path)
    note = root / "plan.md"
    note.write_text("oldword [[Future]]")
    vault.index()
    original = vault.search("oldword")[0]
    assert vault.index()["skipped"] == 1
    note.write_text("newword [[Present]]")
    assert vault.index()["updated"] == 1
    assert vault.search("oldword") == []
    assert vault.search("newword")[0]["source"] == original["source"]
    assert [e["target_label"] for e in vault.graph()["edges"]] == ["Present"]
    note.unlink()
    assert vault.index()["deleted"] == 1
    assert vault.search("newword") == []
    assert vault.graph() == {"nodes": [], "edges": []}


def test_excludes_secrets_system_links_and_database(tmp_path):
    from jarvis.vault import Vault

    root = tmp_path / "notes"
    root.mkdir()
    for name in [".env", ".env.local", "credentials.json", "access-token.txt", "token.json"]:
        (root / name).write_text("confidentialsecret")
    for name in [".ssh", ".aws", ".git"]:
        (root / name).mkdir()
        (root / name / "private.txt").write_text("confidentialsecret")
    outside = tmp_path / "outside.txt"
    outside.write_text("confidentialsecret")
    (root / "outside.txt").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (root / "allowed.txt").write_text("allowedword")
    vault = Vault([root], root / "vault.sqlite")
    assert vault.index()["indexed"] == 1
    assert vault.search("confidentialsecret") == []
    assert len(vault.search("allowedword")) == 1
    with pytest.raises(ValueError):
        Vault([root / "linked-dir"], tmp_path / "bad.sqlite")
    with pytest.raises(ValueError):
        Vault([Path("/etc")], tmp_path / "bad.sqlite")


def test_symlinked_ancestor_and_database_are_denied(tmp_path):
    from jarvis.vault import Vault

    real = tmp_path / "real"
    real.mkdir()
    (real / "notes").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        Vault([alias / "notes"], tmp_path / "db.sqlite")
    db_target = tmp_path / "important.txt"
    db_target.write_text("preserve")
    db_link = tmp_path / "db.sqlite"
    db_link.symlink_to(db_target)
    with pytest.raises(ValueError):
        Vault([real / "notes"], db_link)
    assert db_target.read_text() == "preserve"


def test_enforces_size_limit_and_accepts_json_csv_text(tmp_path):
    vault, root = make_vault(tmp_path, max_file_bytes=80)
    (root / "large.md").write_text("toolarge " * 100)
    (root / "notes.txt").write_text("plaintext")
    (root / "table.csv").write_text("name,value\ncsvmarker,42")
    (root / "data.json").write_text('{"jsonmarker": "value"}')
    (root / "binary.txt").write_bytes(b"secret\0binary")
    (root / "bad.json").write_text("invalid { json")
    counts = vault.index()
    assert counts["indexed"] == 3
    assert counts["skipped"] >= 2
    assert counts["errors"] == 1
    assert vault.search("toolarge") == []
    for word in ["plaintext", "csvmarker", "jsonmarker"]:
        assert vault.search(word)


@pytest.mark.parametrize(
    "query",
    ["", "   ", "!!!", '"', "roadmap) OR (", "'; DROP TABLE sources; --", "roadmap:future", "roadmap AND"],
)
def test_handles_untrusted_fts_input_without_syntax_errors(tmp_path, query):
    vault, root = make_vault(tmp_path)
    (root / "plan.md").write_text("roadmap future AND sources")
    vault.index()
    assert isinstance(vault.search(query), list)
    assert vault.search("roadmap")


def test_graph_records_link_evidence_and_export_is_portable(tmp_path):
    vault, root = make_vault(tmp_path)
    alpha = root / "Alpha.md"
    alpha.write_text("# Alpha\nConnected to [[Beta|the other note]] and [[Missing]].")
    (root / "Beta.md").write_text("# Beta\nA second document.")
    (root / "plain.txt").write_text("[[NotMarkdown]]")
    vault.index()
    graph = vault.graph()
    assert len(graph["nodes"]) == 4  # three sources and one unresolved link
    assert len(graph["edges"]) == 2
    edge = next(e for e in graph["edges"] if e["target_label"] == "Beta")
    assert edge["source"] and edge["target"] and edge["chunk_id"]
    assert "[[Beta|the other note]]" in edge["evidence"]
    beta_node = next(n for n in graph["nodes"] if n.get("path") == str(root / "Beta.md"))
    assert edge["target"] == beta_node["id"]
    destination = tmp_path / "export"
    result = vault.export(destination)
    assert result["sources"] == 3 and result["edges"] == 2
    assert (destination / "index.md").exists()
    assert "[[Beta|the other note]]" in "\n".join(p.read_text() for p in destination.glob("sources/*.md"))
    assert str(root) not in (destination / "index.md").read_text()


def test_export_refuses_symlink_or_existing_output(tmp_path):
    vault, root = make_vault(tmp_path)
    (root / "plan.md").write_text("roadmap")
    vault.index()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        vault.export(linked)
    destination = tmp_path / "export"
    destination.mkdir()
    (destination / "keep.md").write_text("keep")
    with pytest.raises(ValueError):
        vault.export(destination)
    assert (destination / "keep.md").read_text() == "keep"


def test_deleted_root_removes_citations_and_graph(tmp_path):
    vault, root = make_vault(tmp_path)
    note = root / "plan.md"
    note.write_text("roadmap [[Future]]")
    vault.index()
    note.unlink()
    root.rmdir()
    assert vault.index()["deleted"] == 1
    assert vault.search("roadmap") == []
    assert vault.graph() == {"nodes": [], "edges": []}


def test_root_replaced_by_symlink_never_keeps_or_reads_evidence(tmp_path):
    vault, root = make_vault(tmp_path)
    (root / "plan.md").write_text("oldword")
    vault.index()
    (root / "plan.md").unlink()
    root.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "plan.md").write_text("newword")
    root.symlink_to(outside, target_is_directory=True)
    counts = vault.index()
    assert counts["errors"] == 1
    assert counts["deleted"] == 1
    assert vault.search("oldword") == vault.search("newword") == []


def test_invalid_pdf_reports_error_and_continues(tmp_path):
    pytest.importorskip("pypdf")
    vault, root = make_vault(tmp_path)
    (root / "broken.pdf").write_bytes(b"%PDF-1.7\nnot a real document")
    (root / "working.txt").write_text("workingword")
    counts = vault.index()
    assert counts["errors"] == 1
    assert counts["indexed"] == 1
    assert vault.search("workingword")


def test_optional_pdf_extracts_real_page_text(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    vault, root = make_vault(tmp_path)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (pdfmarker evidence) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with (root / "document.pdf").open("wb") as output:
        writer.write(output)
    assert vault.index()["indexed"] == 1
    assert "pdfmarker" in vault.search("pdfmarker")[0]["text"]


def test_graph_resolves_dotted_names_and_relative_links(tmp_path):
    vault, root = make_vault(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "Alpha.md").write_text("[[../v1.0]]")
    target = root / "v1.0.md"
    target.write_text("[[sub/Alpha]]")
    vault.index()
    graph = vault.graph()
    assert len(graph["nodes"]) == 2
    assert all(e["target"] in {n["id"] for n in graph["nodes"]} for e in graph["edges"])


def test_reopening_database_preserves_citations(tmp_path):
    from jarvis.vault import Vault

    vault, root = make_vault(tmp_path)
    (root / "plan.md").write_text("persistedword")
    vault.index()
    original = vault.search("persistedword")
    reopened = Vault([root], vault.db_path)
    assert reopened.search("persistedword") == original
    assert reopened.index()["skipped"] == 1


def test_file_that_becomes_symlink_or_oversize_loses_stale_content(tmp_path):
    vault, root = make_vault(tmp_path, max_file_bytes=80)
    note = root / "plan.md"
    note.write_text("oldword")
    vault.index()
    note.write_text("oversize " * 50)
    assert vault.index()["deleted"] == 1
    assert vault.search("oldword") == []
    note.write_text("newword")
    vault.index()
    note.unlink()
    note.symlink_to(tmp_path / "no-such-file")
    assert vault.index()["deleted"] == 1
    assert vault.search("newword") == []


def test_credential_filename_variants_and_hardlinks_are_excluded(tmp_path):
    vault, root = make_vault(tmp_path)
    for name in ["google-credentials.json", "access-token-backup.txt", ".ENV.production", "secrets.yaml"]:
        (root / name).write_text('{"sensitiveword": true}')
    outside = tmp_path / "outside.txt"
    outside.write_text("sensitiveword")
    os.link(outside, root / "innocent.txt")
    vault.index()
    assert vault.search("sensitiveword") == []


def test_graph_prefers_exact_relative_path_over_ambiguous_title(tmp_path):
    vault, root = make_vault(tmp_path)
    (root / "sub").mkdir()
    (root / "other").mkdir()
    (root / "sub" / "Alpha.md").write_text("[[Beta]]")
    (root / "sub" / "Beta.md").write_text("local beta")
    (root / "other" / "Beta.md").write_text("other beta")
    vault.index()
    graph = vault.graph()
    target = next(n for n in graph["nodes"] if n.get("path") == str(root / "sub" / "Beta.md"))
    assert graph["edges"][0]["target"] == target["id"]


def test_graph_is_bounded_and_search_caps_results(tmp_path):
    vault, root = make_vault(tmp_path)
    (root / "many.md").write_text(" ".join(f"roadmap [[Note{i}]]" for i in range(2100)))
    vault.index()
    graph = vault.graph()
    assert len(graph["nodes"]) <= 1000
    assert len(graph["edges"]) <= 2000
    assert len(vault.search("roadmap", limit=10000)) <= 100


def test_database_hardlink_cannot_modify_unrelated_file(tmp_path):
    from jarvis.vault import Vault

    root = tmp_path / "notes"
    root.mkdir()
    other = tmp_path / "other.txt"
    other.write_text("preserve")
    mode = other.stat().st_mode
    linked = tmp_path / "vault.sqlite"
    os.link(other, linked)
    with pytest.raises(ValueError):
        Vault([root], linked)
    assert other.read_text() == "preserve"
    assert other.stat().st_mode == mode


@pytest.mark.parametrize("root", ["/usr", "/usr/share", "/var/log"])
def test_system_document_trees_cannot_be_authorized(tmp_path, root):
    from jarvis.vault import Vault

    with pytest.raises(ValueError):
        Vault([Path(root)], tmp_path / "db.sqlite")


def test_changed_root_configuration_revokes_old_citations_immediately(tmp_path):
    from jarvis.vault import Vault

    vault, root = make_vault(tmp_path)
    (root / "plan.md").write_text("oldscopeword [[Future]]")
    vault.index()
    changed = Vault([], vault.db_path)
    assert changed.search("oldscopeword") == []
    assert changed.graph() == {"nodes": [], "edges": []}
    assert changed.export(tmp_path / "export")["sources"] == 0
