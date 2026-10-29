"""첨부 파일 글자 추출 테스트 (DOCX·HWPX는 테스트 안에서 만들어 쓴다)."""
import io
import zipfile

import docx
import pytest

from deadline.fileread import FileReadError, read_file

LINES = ["사 건 2026가합123456", "판결정본은 2026. 9. 11. 송달되었음을 증명합니다."]


def test_docx():
    d = docx.Document()
    for l in LINES:
        d.add_paragraph(l)
    buf = io.BytesIO()
    d.save(buf)
    assert "2026. 9. 11." in read_file("a.docx", buf.getvalue())


def test_hwpx():
    xml = ('<s xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
           + "".join(f"<hp:p><hp:run><hp:t>{l}</hp:t></hp:run></hp:p>" for l in LINES) + "</s>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", xml)
    assert "송달되었음" in read_file("a.hwpx", buf.getvalue())


def test_control_chars_become_spaces():
    assert read_file("a.txt", "수령일:\x012026.\x0111.\x012.".encode()) == "수령일: 2026. 11. 2."


@pytest.mark.parametrize("name,data", [("a.exe", b"x" * 20), ("a.pdf", b"x" * (5 * 1024 * 1024 + 1)), ("a.txt", b"  ")])
def test_rejects(name, data):
    with pytest.raises(FileReadError):
        read_file(name, data)
