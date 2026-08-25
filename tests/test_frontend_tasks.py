from pathlib import Path


TASKS_TSX = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "Tasks.tsx"
TASK_LOG_PANEL_TSX = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "tasks"
    / "TaskLogPanel.tsx"
)


def test_tasks_page_does_not_live_stream_finished_logs():
    source = TASKS_TSX.read_text(encoding="utf-8")
    assert "sameTaskSnapshot" in source
    assert "hasRunning ? 2500 : 8000" in source
    assert "visibilitychange" in source
    assert "查看日志" in source
    assert "live={live}" in source
    assert "window.setInterval(() => void load(), 1000)" not in source


def test_task_log_panel_skips_sse_when_not_live():
    source = TASK_LOG_PANEL_TSX.read_text(encoding="utf-8")
    assert "live = true" in source
    assert "if (!live)" in source
    assert "new EventSource" in source
    assert source.count("new EventSource") == 1
