import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
from io import BytesIO

from toolkit.bot import accept, action, build_app, preview, restore, text, upload
from toolkit.core import reader
from toolkit.store import Store
from test_core import pdf


def run(coro):
    return asyncio.run(coro)


def environment(tmp_path):
    db = Store(str(tmp_path))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={'store': db}), bot_data={'timeout': 1800})
    message = SimpleNamespace(text='', reply_text=AsyncMock(), reply_document=AsyncMock(), reply_photo=AsyncMock(), delete=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=message)
    return db, context, message, update


def test_chained_actions_and_delivery(tmp_path):
    db, ctx, message, update = environment(tmp_path)
    accept(7, ctx, pdf(), 'first.pdf')
    accept(7, ctx, pdf((500,)), 'second.pdf')
    state = db.get(7)
    state['mode'] = 'remove'
    db.save(7, state)
    message.text = '2'
    run(text(update, ctx))
    assert len(reader(db.read(7, db.get(7)['work'])).pages) == 2
    run(restore(message, ctx, 7, 'undo'))
    assert len(reader(db.read(7, db.get(7)['work'])).pages) == 3
    for mode, value in [('source', '2'), ('pages', '1'), ('position', 'after page 1'), ('reorder', 'reverse'), ('rename', 'Invoice_{date}')]:
        state = db.get(7)
        state['mode'] = mode
        db.save(7, state)
        message.text = value
        run(text(update, ctx))
    assert [p.mediabox.width for p in reader(db.read(7, db.get(7)['work'])).pages] == [300, 200, 500, 100]
    run(preview(message, ctx, 7, 0))
    message.reply_photo.assert_awaited_once()
    state = db.get(7)
    state['mode'] = 'done'
    db.save(7, state)
    message.text = '.'
    run(text(update, ctx))
    message.reply_document.assert_awaited_once()
    assert db.get(7) is None
    assert not (tmp_path / '7').exists()


def test_first_password_upload(tmp_path):
    db, ctx, message, update = environment(tmp_path)
    data = pdf(password='secret')
    remote = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
    message.document = SimpleNamespace(file_name='locked.pdf', mime_type='application/pdf', file_size=len(data), get_file=AsyncMock(return_value=remote))
    message.photo = []
    run(upload(update, ctx))
    assert db.get(7)['mode'] == 'password'
    message.text = 'secret'
    run(text(update, ctx))
    assert db.get(7)['work'] == db.get(7)['original']
    assert not (tmp_path / '7' / 'locked.pdf').exists()
    message.delete.assert_awaited_once()


def test_photo_source(tmp_path):
    db, ctx, message, update = environment(tmp_path)
    image = BytesIO()
    Image.new('RGB', (100, 100), 'red').save(image, 'JPEG')
    data = image.getvalue()
    remote = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
    message.document = None
    message.photo = [SimpleNamespace(file_size=len(data), get_file=AsyncMock(return_value=remote))]
    run(upload(update, ctx))
    assert len(reader(db.read(7, db.get(7)['work'])).pages) == 1


def test_application_builds(tmp_path):
    app = build_app('123456:fake-token-for-local-testing', Store(str(tmp_path)), 1800)
    assert app.job_queue is not None
    assert len(app.handlers[0]) == 5


def test_failed_delivery_keeps_session(tmp_path):
    db, ctx, message, update = environment(tmp_path)
    state = accept(7, ctx, pdf(), 'first.pdf')
    state['mode'] = 'done'
    db.save(7, state)
    message.text = '.'
    message.reply_document.side_effect = RuntimeError('network unavailable')
    with pytest.raises(RuntimeError):
        run(text(update, ctx))
    assert db.get(7)['work'] == state['work']
    assert db.read(7, state['work'])
