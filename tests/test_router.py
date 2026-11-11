"""Routing tests: which lines open instantly, which go to the shell, which go to Claude.
Run:  python -m pytest tests   (Windows only)"""
import os, sys, tempfile
from pathlib import Path

os.environ["ZAP_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import zap  # noqa: E402

LAUNCHED = []
zap.launch = LAUNCHED.append
zap.webbrowser.open = LAUNCHED.append
zap.APPS.clear()
zap.APPS.update({"spotify": "shell:AppsFolder\\Spotify", "google chrome": "shell:AppsFolder\\Chrome",
                 "obsidian": "shell:AppsFolder\\md.obsidian", "capcut": "C:\\x\\CapCut.lnk"})

def opens(line):
    LAUNCHED.clear()
    return zap.try_fast(line) and LAUNCHED

def test_aliases_and_apps():
    assert opens("paint") == ["mspaint"]
    assert opens("open paint") == ["mspaint"]
    assert opens("Open Spotify") == ["shell:AppsFolder\\Spotify"]
    assert opens("chrome") == ["shell:AppsFolder\\Chrome"]        # word inside "google chrome"
    assert opens("launch the obsidian app") == ["shell:AppsFolder\\md.obsidian"]

def test_typos():
    assert opens("spotfy") == ["shell:AppsFolder\\Spotify"]
    assert opens("obsidain") == ["shell:AppsFolder\\md.obsidian"]

def test_sites_and_search():
    assert opens("youtube") == ["https://youtube.com"]
    assert opens("google best pizza")[0].endswith("q=best+pizza")
    assert "search_query=lofi" in opens("yt lofi")[0]

def test_user_alias():
    zap.CFG["aliases"] = {"work": "C:\\Projects"}
    assert opens("work") == ["C:\\Projects"]

def test_sentences_go_to_claude():
    for line in ["what is the weather in paris", "find my newest video", "open chrome on my work profile",
                 "how much space is left on c", "turn the volume down"]:
        assert not opens(line), line

def test_shell_detection():
    for line in ["dir", "git status", "ping google.com", "cd ..", "npm run dev", "Get-Process | sort cpu"]:
        assert not zap.try_fast(line) and zap.looks_like_shell(line), line
    for line in ["find the biggest files in downloads", "make a folder called test on my desktop"]:
        assert not zap.looks_like_shell(line), line

def test_clean_env_drops_parent_session():
    os.environ["CLAUDECODE"] = "1"; os.environ["CLAUDE_CODE_CHILD_SESSION"] = "1"
    os.environ["ANTHROPIC_BASE_URL"] = "http://proxy"
    env = zap.clean_env()
    assert not any(k.startswith("CLAUDE") for k in env) and "ANTHROPIC_BASE_URL" not in env

def test_word_typo():
    assert opens("chorme") == ["shell:AppsFolder\Chrome"]
