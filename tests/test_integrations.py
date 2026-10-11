import pytest


def test_website_tool_produces_real_artifact_without_invented_claims(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    receipt = ToolService(settings).execute("create_website", {
        "path": "website.html", "business": "Owner supplied business", "title": "Acme"})
    assert receipt["verified"]
    page = (settings.workspace / "website.html").read_text()
    assert "Owner supplied business" in page
    assert "Acme" in page
    assert "cdn.jsdelivr.net" not in page


def test_business_tool_reports_verified_real_csv(tmp_path):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    (settings.workspace / "sales.csv").write_text("revenue,cost\n100.25,50.15\n20,10\n")
    result = ToolService(settings).execute("business_report", {"path": "sales.csv"})
    assert result["profit"] == "60.10"
    assert result["verified"]


def test_optional_outbound_tools_not_enabled_by_presence_of_credentials(tmp_path, monkeypatch):
    from jarvis.config import Settings
    from jarvis.tools import ToolService
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:configured")
    settings = Settings(data_dir=tmp_path / "state", workspace=tmp_path / "work")
    settings.prepare()
    with pytest.raises(ValueError):
        ToolService(settings).execute("notify", {"text": "send", "channel": "telegram", "key": "request"})
