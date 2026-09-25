"""HTML → PDF（Windows Edge Headless）。"""

import subprocess
import tempfile
from pathlib import Path

_EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def _find_edge() -> Path | None:
    for p in _EDGE_CANDIDATES:
        if p.is_file():
            return p
    return None


def save_html_as_pdf(html: str, output_path: Path) -> None:
    """将完整 HTML 文档保存为 PDF。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    edge = _find_edge()
    if edge is None:
        raise RuntimeError("未找到 Microsoft Edge，无法直接导出 PDF，请安装 Edge 或留空 PDF 保存路径改用浏览器打印")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8",
    ) as tmp:
        tmp.write(html)
        html_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            [
                str(edge),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={output_path}",
                html_path.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0 or not output_path.is_file():
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Edge 导出 PDF 失败{(': ' + err) if err else ''}")
    finally:
        html_path.unlink(missing_ok=True)
