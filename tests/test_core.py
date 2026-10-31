from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from toolkit.core import InputError, PasswordRequired, assemble, filename, permutation, ranges, reader, reorder, unlock
from toolkit.store import Store


def pdf(widths=(100, 200, 300), password=None):
    writer = PdfWriter()
    for width in widths:
        page = writer.add_blank_page(width, 400)
        stream = DecodedStreamObject()
        stream.set_data(b"q 1 0 0 1 0 0 cm Q")
        page[NameObject('/Contents')] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_structural_edits_preserve_content():
    source = pdf()
    result = reader(assemble([(source, [2, 0]), (pdf((500,)), None)]))
    assert [p.mediabox.width for p in result.pages] == [300, 100, 500]
    assert all(p.get_contents().get_data() == reader(source).pages[0].get_contents().get_data() for p in result.pages)


@pytest.mark.parametrize('value', ['0', '4', '2-1', '1,1', '', 'a', '1-999999999'])
def test_bad_ranges(value):
    with pytest.raises(InputError):
        ranges(value, 3)


def test_order():
    assert ranges('1, 3-4', 4) == [0, 2, 3]
    assert reorder('reverse', 3) == [2, 1, 0]
    assert reorder('move 3 to 1', 3) == [2, 0, 1]
    with pytest.raises(InputError):
        permutation('1,2', 3)


def test_encryption():
    data = pdf(password='secret')
    with pytest.raises(PasswordRequired):
        unlock(data)
    with pytest.raises(PasswordRequired):
        unlock(data, 'wrong')
    assert not reader(unlock(data, 'secret')).is_encrypted


def test_names():
    assert filename('../CON.pdf') == '_CON.pdf'
    assert filename('a/b:c*?') == 'abc.pdf'
    assert filename('  ') == 'document.pdf'
    assert '{date}' not in filename('Invoice_{date}')


def test_persistence_and_cleanup(tmp_path):
    db = Store(str(tmp_path))
    original = db.put(1, pdf())
    state = dict(work=original, original=original, history=[], name='a.pdf', mode='menu')
    db.save(1, state)
    db.commit(1, state, assemble([(pdf(), [2, 0])]))
    assert state['history'] == [[original, 'a.pdf']]
    assert len(reader(db.read(1, state['work'])).pages) == 2
    assert Store(str(tmp_path)).get(1)['work'] == state['work']
    db.expire(-1)
    assert db.get(1) is None
    assert not (tmp_path / '1').exists()


def test_page_limit_atomic(tmp_path):
    db = Store(str(tmp_path), max_pages=2)
    with pytest.raises(InputError):
        db.put(1, pdf())
    assert list((tmp_path / '1').iterdir()) == []
