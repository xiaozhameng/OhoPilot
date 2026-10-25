"""文档解析：真生成 PDF/docx/xlsx 再解析回来。

生成文件用的是和解析相同的库（pypdf 只能提取文本，写入用 reportlab 太重，
所以 PDF 测试用 pypdf 手工构造最小文档；docx/xlsx 直接用对应库生成）。
"""
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import Workbook
from docx import Document as DocxDocument

from ohopilot.config import Config
from ohopilot.tools.docs import ReadDocumentTool

def _make_pdf(path: Path, text: str = "Hello OhoPilot PDF") -> None:
    """程序化构造一个合法的最小 PDF。

    不能手写死字节：PDF 的 xref 表要求每个对象的**字节偏移**精确，
    手写必错（第一版就死于 startxref not found）。这里逐对象写出并
    实时记录偏移，最后算出 xref——pypdf 才认。
    """
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_pos,
    )
    path.write_bytes(bytes(out))


@pytest.fixture
def tool(tmp_path):
    cfg = Config(workspace=tmp_path, home=tmp_path / "home")
    return ReadDocumentTool(cfg)


def _make_docx(path: Path) -> None:
    doc = DocxDocument()
    doc.add_paragraph("本季度销售总结")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "产品"
    table.rows[0].cells[1].text = "销售额"
    table.rows[1].cells[0].text = "A"
    table.rows[1].cells[1].text = "1200"
    doc.save(path)


def _make_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "一月"
    ws.append(["日期", "金额"])
    ws.append(["2026-01-05", 300])
    ws.append([None, None])  # 空行，应当被剪掉
    ws.append(["2026-01-06", 450])
    ws2 = wb.create_sheet("二月")
    ws2.append(["日期", "金额"])
    ws2.append(["2026-02-01", 500])
    wb.save(path)


# ---------- 通用边界 ----------

async def test_不存在的文件给补救建议(tool):
    result = await tool.safe_run({"path": "无/abc.pdf"})
    assert result.is_error
    assert "fs_search" in result.content


async def test_不支持的格式指路_read_file(tool, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("纯文本", encoding="utf-8")
    result = await tool.safe_run({"path": str(f)})
    assert result.is_error
    assert "fs_read_file" in result.content


async def test_受保护路径拒绝(tool, tmp_path):
    f = tmp_path / ".env"
    f.write_text("KEY=1", encoding="utf-8")
    result = await tool.safe_run({"path": str(f)})
    assert result.is_error
    assert "受保护" in result.content


async def test_工作目录外的文档也能读(tool, tmp_path):
    """读不受工作目录限制（它是写操作的边界），和 fs_read_file 口径一致；
    只有受保护路径（.env/.git）例外。"""
    outside = tmp_path.parent / f"outside_{uuid4().hex[:6]}.pdf"
    _make_pdf(outside)
    result = await tool.safe_run({"path": str(outside)})
    assert not result.is_error
    assert "Hello OhoPilot PDF" in result.content
    outside.unlink(missing_ok=True)


# ---------- PDF ----------

async def test_pdf_全文读取(tool, tmp_path):
    f = tmp_path / "a.pdf"
    _make_pdf(f)
    result = await tool.safe_run({"path": str(f)})
    assert not result.is_error
    assert "共 1 页" in result.content
    assert "Hello OhoPilot PDF" in result.content


async def test_pdf_越界页码带范围提示(tool, tmp_path):
    f = tmp_path / "a.pdf"
    _make_pdf(f)
    result = await tool.safe_run({"path": str(f), "page": 9})
    assert not result.is_error  # 越界是给提示不是报错，模型才能自己修正
    assert "共 1 页" in result.content
    assert "超出范围" in result.content


# ---------- Word ----------

async def test_docx_段落与表格都读出来且顺序正确(tool, tmp_path):
    f = tmp_path / "a.docx"
    _make_docx(f)
    result = await tool.safe_run({"path": str(f)})
    assert not result.is_error
    assert "销售总结" in result.content
    assert "[表格 1]" in result.content
    assert "1200" in result.content
    # 段落在表格前（真实文档顺序）
    assert result.content.index("销售总结") < result.content.index("[表格 1]")


# ---------- Excel ----------

async def test_xlsx_全部工作表且空行被剪(tool, tmp_path):
    f = tmp_path / "a.xlsx"
    _make_xlsx(f)
    result = await tool.safe_run({"path": str(f)})
    assert not result.is_error
    assert "[工作表 一月]" in result.content
    assert "[工作表 二月]" in result.content
    assert "2026-01-06" in result.content
    # 空行剪掉后，最后一条数据就是一月表的最后一行
    assert result.content.count("2026-01-05\t300") == 1


async def test_xlsx_指定工作表(tool, tmp_path):
    f = tmp_path / "a.xlsx"
    _make_xlsx(f)
    result = await tool.safe_run({"path": str(f), "sheet": "二月"})
    assert not result.is_error
    assert "2026-02-01" in result.content
    assert "[工作表 一月]" not in result.content


async def test_xlsx_不存在的表名列出所有表(tool, tmp_path):
    f = tmp_path / "a.xlsx"
    _make_xlsx(f)
    result = await tool.safe_run({"path": str(f), "sheet": "不存在"})
    assert not result.is_error
    assert "一月" in result.content and "二月" in result.content


async def test_超长内容按上限截断并说明(tool, tmp_path):
    doc = DocxDocument()
    doc.add_paragraph("长" * 5000)
    doc.add_paragraph("尾部内容")
    f = tmp_path / "long.docx"
    doc.save(f)
    result = await tool.safe_run({"path": str(f), "max_chars": 3000})
    assert not result.is_error
    assert "截断" in result.content
    assert "尾部内容" not in result.content
