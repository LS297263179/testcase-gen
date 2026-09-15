"""V2 Ingestion Layer 测试 - 文本/Markdown/图片归一化、容错、组合入口。

对应 Step 2 Ingestion；PDF/Word 按确认延后，此处不测。
"""

import base64

from core.v2.ingestion import ingest, ingest_paths, normalize_text


class TestNormalizeText:
    def test_unifies_newlines(self):
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"

    def test_compresses_blank_lines(self):
        # 连续空行压成单个
        assert normalize_text("a\n\n\n\nb") == "a\n\nb"

    def test_strips_and_rstrip(self):
        assert normalize_text("  \n  hello  \n  ") == "hello"

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""


class TestIngestPaths:
    def test_text_file(self, tmp_path):
        p = tmp_path / "req.txt"
        p.write_text("需求第一行\n需求第二行", encoding="utf-8")
        text, images = ingest_paths([str(p)])
        assert "需求第一行" in text and "需求第二行" in text
        assert images == []

    def test_markdown_file(self, tmp_path):
        p = tmp_path / "req.md"
        p.write_text("# 标题\n- 要点A\n- 要点B", encoding="utf-8")
        text, _ = ingest_paths([str(p)])
        assert "# 标题" in text and "要点B" in text

    def test_image_file(self, tmp_path):
        p = tmp_path / "ui.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n_fake_bytes_")  # 仅需扩展名+字节，走 base64
        text, images = ingest_paths([str(p)])
        assert text == ""
        assert len(images) == 1
        img = images[0]
        assert img["media_type"] == "image/png"
        assert img["filename"] == "ui.png"
        assert base64.b64decode(img["data"]) == b"\x89PNG\r\n\x1a\n_fake_bytes_"

    def test_mixed_text_and_image(self, tmp_path):
        t = tmp_path / "r.txt"
        t.write_text("文字需求", encoding="utf-8")
        i = tmp_path / "s.png"
        i.write_bytes(b"x")
        text, images = ingest_paths([str(t), str(i)])
        assert "文字需求" in text
        assert len(images) == 1

    def test_missing_file_skipped_not_crash(self, tmp_path):
        # 不存在的文件应被跳过（降级告警），不影响其它文件
        good = tmp_path / "ok.txt"
        good.write_text("有效内容", encoding="utf-8")
        text, images = ingest_paths([str(tmp_path / "不存在.txt"), str(good)])
        assert "有效内容" in text
        assert images == []

    def test_empty_input(self):
        assert ingest_paths(None) == ("", [])
        assert ingest_paths([]) == ("", [])


class TestIngest:
    def test_text_only(self):
        text, images = ingest(text="  直接文本  ")
        assert text == "直接文本"
        assert images == []

    def test_text_plus_paths_merged(self, tmp_path):
        p = tmp_path / "extra.md"
        p.write_text("文件补充内容", encoding="utf-8")
        text, images = ingest(text="主需求", paths=[str(p)])
        assert "主需求" in text and "文件补充内容" in text
        # 主文本在前，文件内容在后
        assert text.index("主需求") < text.index("文件补充内容")

    def test_both_empty(self):
        assert ingest() == ("", [])
