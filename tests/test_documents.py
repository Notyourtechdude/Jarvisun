"""PDF parsing runs in a real bounded child process."""

import json
from pathlib import Path

import pytest

pytest.importorskip("pypdf", reason="Optional PDF document dependency is not installed")


def pdf_bytes():
    import io

    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

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
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (childpdfmarker evidence) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_extraction_uses_real_resource_limited_child():
    from jarvis.document_worker import extract_pdf

    assert "childpdfmarker" in extract_pdf(pdf_bytes(), max_input_bytes=20_000)


def test_malformed_pdf_is_explicit_failure():
    from jarvis.document_worker import extract_pdf

    with pytest.raises(ValueError):
        extract_pdf(b"%PDF-1.7\nbroken", max_input_bytes=2000)


def test_input_cap_is_checked_before_launch():
    from jarvis.document_worker import extract_pdf

    with pytest.raises(ValueError, match="input"):
        extract_pdf(b"a" * 1001, max_input_bytes=1000)


def test_timeout_kills_child_without_waiting_for_completion(tmp_path, monkeypatch):
    from jarvis import document_worker

    worker = tmp_path / "sleep.py"
    worker.write_text("import time; time.sleep(5)")
    monkeypatch.setattr(document_worker, "_WORKER_SCRIPT", worker)
    with pytest.raises(ValueError, match="time"):
        document_worker.extract_pdf(b"x", max_input_bytes=1000, timeout=0.1)


def test_child_credentials_are_not_copied_and_limits_are_active(tmp_path, monkeypatch):
    from jarvis import document_worker

    monkeypatch.setenv("JARVIS_MODEL_API_KEY", "privatecredential")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "privatecredential")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    worker = tmp_path / "inspect.py"
    original = Path(document_worker.__file__)
    worker.write_text(f"""import runpy, os, json, resource
namespace = runpy.run_path({str(original)!r}, run_name='limits_test')
namespace['_set_limits']()
facts = {{'credentials': [name for name in os.environ if name in ['JARVIS_MODEL_API_KEY', 'TELEGRAM_BOT_TOKEN', 'PYTHONPATH']], 'cpu': resource.getrlimit(resource.RLIMIT_CPU)[0], 'memory': resource.getrlimit(resource.RLIMIT_AS)[0], 'file': resource.getrlimit(resource.RLIMIT_FSIZE)[0]}}
print(json.dumps({{'text': json.dumps(facts)}}))
""")
    monkeypatch.setattr(document_worker, "_WORKER_SCRIPT", worker)
    facts = json.loads(document_worker.extract_pdf(b"x", max_input_bytes=1000))
    assert facts == {"credentials": [], "cpu": 5, "memory": 512 * 1024 * 1024, "file": 8_000_000}


def test_output_is_temp_backed_and_capped(tmp_path, monkeypatch):
    from jarvis import document_worker

    worker = tmp_path / "output.py"
    worker.write_text("import sys; sys.stdout.write('a' * 8_000_001)")
    monkeypatch.setattr(document_worker, "_WORKER_SCRIPT", worker)
    with pytest.raises(ValueError, match="output"):
        document_worker.extract_pdf(b"x", max_input_bytes=1000)


def test_vault_does_not_parse_pdf_in_application_process(tmp_path, monkeypatch):
    import pypdf

    from jarvis.vault import Vault

    data = pdf_bytes()

    def parent_parser_denied(*args, **kwargs):
        raise ValueError("PDF parsing happened in the application")

    monkeypatch.setattr(pypdf, "PdfReader", parent_parser_denied)
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "document.pdf").write_bytes(data)
    vault = Vault([root], tmp_path / "vault.sqlite3")
    assert vault.index()["indexed"] == 1
    assert vault.search("childpdfmarker")


def test_child_address_space_limit_blocks_large_allocation(tmp_path, monkeypatch):
    from jarvis import document_worker

    original = Path(document_worker.__file__)
    worker = tmp_path / "allocate.py"
    worker.write_text(f"""import runpy, json
runpy.run_path({str(original)!r}, run_name='limits_test')['_set_limits']()
try:
    allocation = bytearray(600 * 1024 * 1024)
except MemoryError:
    print(json.dumps({{'text': 'allocation bounded'}}))
else:
    print(json.dumps({{'text': 'unbounded'}}))
""")
    monkeypatch.setattr(document_worker, "_WORKER_SCRIPT", worker)
    assert document_worker.extract_pdf(b"x", max_input_bytes=1000) == "allocation bounded"
