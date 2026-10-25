"""文档解析工具：PDF / Word / Excel → 纯文本。

为什么单独一个工具而不是塞进 fs_read_file：
fs_read_file 的合同是「读文本文件」，把二进制解出来是另一套活
（依赖三个解析库、要分页/分表截断），混在一起两边都变复杂。

依赖哲学的落实：三个解析库是可选组 [docs]（pip install "ohopilot[docs]"）。
所有 import 都放在函数内惰性执行——不装这三个库，本模块照常导入，
只有真的解析对应格式时才会报「缺依赖」，且错误信息带安装命令。
（import 放函数里还有一个原因：惰性失败让测试可以逐格式跑，不用整体跳过。）
"""
from pathlib import Path
from typing import Any

from ohopilot.config import Config
from ohopilot.tools.base import Tool, ToolResult
from ohopilot.tools.fs import _FsTool

#: 单次返回的字符上限。模型上下文有限，一份 300 页 PDF 全文几十万字符，
#: 一把塞进去必爆 token；超限截断时头部会说明，模型可用 page 参数翻页。
DEFAULT_MAX_CHARS = 20_000
MAX_MAX_CHARS = 50_000

#: xlsx 每个 sheet 最多读多少行——报表前几千行往往是格式占位
MAX_ROWS_PER_SHEET = 200
MAX_SHEETS = 20

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx"}


class _DependencyMissing(Exception):
    """缺解析库。单独定义而不是直接返回 fail：解析器保持纯函数好测，
    缺库的「怎么说」集中在 run() 一处处理。"""

    def __init__(self, lib: str, fmt: str):
        self.hint = (
            f"解析 {fmt} 需要安装 {lib}。安装命令：\n"
            f'  pip install "ohopilot[docs]"\n'
            "装完后重启 oho / oho-server 生效。"
        )
        super().__init__(self.hint)


class ReadDocumentTool(_FsTool):
    name = "fs_read_document"
    description = (
        "读取 PDF / Word(.docx) / Excel(.xlsx) 文档，转成纯文本返回。"
        "适合：读合同、报告、周报、数据表等办公文档。"
        "PDF 默认从头读到截断，指定 page 看某一页；xlsx 用 sheet 参数选工作表。"
        "什么时候不用：纯文本文件（代码、md、csv）用 fs_read_file，"
        "读文档前不知道有哪些文件时先用 fs_search / fs_list_dir 定位。"
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文档路径（.pdf / .docx / .xlsx）"},
            "page": {
                "type": "integer",
                "description": "PDF 专用：只读第几页（从 1 起）；不传就读全部（可能截断）",
            },
            "sheet": {
                "type": "string",
                "description": "xlsx 专用：工作表名；不传就列全部工作表（每个最多 200 行）",
            },
            "max_chars": {
                "type": "integer",
                "description": "返回文本上限，默认 20000，最大 50000",
            },
        },
        "required": ["path"],
    }

    async def run(
        self,
        path: str,
        page: int | None = None,
        sheet: str | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        **_: Any,
    ) -> ToolResult:
        target, _ = self._resolve(path)
        if self._is_protected(target):
            return ToolResult.fail(f"禁止读取受保护路径：{target}")
        if not target.exists():
            return ToolResult.fail(
                f"文件不存在：{target}\n可用 fs_search 找文件，或用 fs_list_dir 看目录里有什么。"
            )

        suffix = target.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return ToolResult.fail(
                f"不支持的文档格式：{suffix}。只支持 {' / '.join(sorted(SUPPORTED_SUFFIXES))}；"
                "纯文本文件请用 fs_read_file。"
            )

        max_chars = min(max(int(max_chars), 1000), MAX_MAX_CHARS)

        try:
            if suffix == ".pdf":
                text, meta = self._read_pdf(target, page)
            elif suffix == ".docx":
                text, meta = self._read_docx(target)
            else:
                text, meta = self._read_xlsx(target, sheet)
        except _DependencyMissing as exc:
            return ToolResult.fail(exc.hint)
        except ValueError as exc:
            # pypdf/openpyxl 对损坏文件抛 ValueError 家族的异常
            return ToolResult.fail(f"文档解析失败（文件可能损坏或加密）：{exc}")
        except OSError as exc:
            return ToolResult.fail(f"读取失败：{exc}")

        note = ""
        if len(text) > max_chars:
            text = text[:max_chars]
            note = f"\n\n（内容已按 {max_chars} 字符截断；{meta['tail_hint']}）"
        header = f"{target}\n{meta['summary']}\n\n"
        return ToolResult.ok(header + text + note)

    # ---------- 三个解析器 ----------

    def _read_pdf(self, target: Path, page: int | None) -> tuple[str, dict[str, str]]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise _DependencyMissing("pypdf", "PDF") from exc

        reader = PdfReader(str(target))
        total = len(reader.pages)
        if total == 0:
            return "", {"summary": "PDF 共 0 页（空文档）", "tail_hint": "本文档没有文本内容"}

        if page is not None:
            if not 1 <= page <= total:
                hint = f"page={page} 超出范围：这份 PDF 共 {total} 页（1-{total}），请改用范围内的页码。"
                return hint, {"summary": f"PDF 共 {total} 页", "tail_hint": "改用范围内的页码"}
            text = reader.pages[page - 1].extract_text() or "（这一页没有可提取的文本）"
            return text, {"summary": f"PDF 共 {total} 页，显示第 {page} 页", "tail_hint": f"用 page 参数看其他页（共 {total} 页）"}

        parts = [f"--- 第 {i + 1} 页 ---\n{p.extract_text() or '（无文本）'}" for i, p in enumerate(reader.pages)]
        return "\n\n".join(parts), {
            "summary": f"PDF 共 {total} 页",
            "tail_hint": f"可用 page 参数指定看第几页（共 {total} 页）",
        }

    def _read_docx(self, target: Path) -> tuple[str, dict[str, str]]:
        try:
            import docx  # python-docx
        except ImportError as exc:
            raise _DependencyMissing("python-docx", "Word") from exc

        document = docx.Document(str(target))
        # 段落和表格要按文档里的真实顺序交错输出——
        # document.paragraphs 和 document.tables 是两个平行列表，
        # 直接各拼一段会打乱「上下文 → 数据表 → 结论」的阅读顺序。
        # document.element.body 按顺序给节点，逐个判类型还原真实顺序。
        lines: list[str] = []
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        body = document.element.body
        para_count = table_count = 0
        for child in body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph = Paragraph(child, document)
                if paragraph.text.strip():
                    lines.append(paragraph.text)
                    para_count += 1
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                table_count += 1
                lines.append(f"\n[表格 {table_count}]")
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    lines.append("\t".join(cells))
                lines.append("")

        return "\n".join(lines), {
            "summary": f"Word 文档：{para_count} 个段落、{table_count} 个表格",
            "tail_hint": "内容有截断，可让用户拆分文档或复制需要的部分",
        }

    def _read_xlsx(self, target: Path, sheet: str | None) -> tuple[str, dict[str, str]]:
        try:
            import openpyxl
        except ImportError as exc:
            raise _DependencyMissing("openpyxl", "Excel") from exc

        # read_only=True 流式读取，几百 MB 的报表不会整个load进内存
        workbook = openpyxl.load_workbook(target, read_only=True, data_only=True)
        try:
            names = workbook.sheetnames[:MAX_SHEETS]
            if sheet is not None:
                if sheet not in workbook.sheetnames:
                    return "", {
                        "summary": f"没有叫「{sheet}」的工作表。现有的：{'、'.join(names)}",
                        "tail_hint": "用 sheet 参数指定上面列表中的名字",
                    }
                names = [sheet]

            blocks: list[str] = []
            for name in names:
                ws = workbook[name]
                rows: list[str] = []
                for row in ws.iter_rows(max_row=MAX_ROWS_PER_SHEET, values_only=True):
                    # 空行剪枝：Excel 里大量「看着空其实有格式」的行，不剪会浪费大量上下文
                    if all(v is None or str(v).strip() == "" for v in row):
                        continue
                    rows.append("\t".join("" if v is None else str(v) for v in row))
                total_rows = ws.max_row or 0
                block = f"[工作表 {name}]（共 {total_rows} 行，显示 {len(rows)} 行）\n" + "\n".join(rows)
                if total_rows > MAX_ROWS_PER_SHEET:
                    block += f"\n（已截断，只读了前 {MAX_ROWS_PER_SHEET} 行）"
                blocks.append(block)

            summary = f"Excel：{len(workbook.sheetnames)} 个工作表（{'、'.join(workbook.sheetnames[:MAX_SHEETS])}）"
            return "\n\n".join(blocks), {"summary": summary, "tail_hint": "用 sheet 参数分次看各工作表"}
        finally:
            workbook.close()


def build_docs_tools(cfg: Config) -> list[Tool]:
    return [ReadDocumentTool(cfg)]
