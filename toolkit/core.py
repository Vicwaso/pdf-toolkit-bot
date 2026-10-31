import re
from datetime import date
from io import BytesIO

from pypdf import PdfReader, PdfWriter


class InputError(ValueError):
    pass


class PasswordRequired(InputError):
    pass


def reader(data: bytes, password: str = "") -> PdfReader:
    try:
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted and not pdf.decrypt(password):
            raise PasswordRequired("This PDF is password-protected. Send its password or /cancel.")
        if not len(pdf.pages):
            raise InputError("This PDF has no pages.")
        return pdf
    except InputError:
        raise
    except Exception as exc:
        raise InputError("I couldn't read that PDF. Please send a valid PDF.") from exc


def ranges(text: str, count: int) -> list[int]:
    if len(text) > 10000:
        raise InputError("That page list is too long.")
    result = []
    for token in text.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", token)
        if not match:
            raise InputError("Use page numbers or ranges, for example: 3, 5-7, 10.")
        if len(match[1]) > 9 or (match[2] and len(match[2]) > 9):
            raise InputError(f"Pages must be between 1 and {count}.")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise InputError(f"Pages must be between 1 and {count}; ranges must run forwards.")
        result.extend(range(start - 1, end))
        if len(result) > count:
            raise InputError("Each page may appear only once.")
    if len(set(result)) != len(result):
        raise InputError("Each page may appear only once.")
    return result


def permutation(text: str, count: int) -> list[int]:
    order = ranges(text, count)
    if sorted(order) != list(range(count)):
        raise InputError(f"Include every number from 1 to {count} exactly once.")
    return order


def reorder(text: str, count: int) -> list[int]:
    if text.strip().lower() == "reverse":
        return list(reversed(range(count)))
    match = re.fullmatch(r"move\s+(\d+)\s+to\s+(\d+)", text.strip(), re.I)
    if match:
        source, target = map(int, match.groups())
        if not (1 <= source <= count and 1 <= target <= count):
            raise InputError(f"Positions must be between 1 and {count}.")
        order = list(range(count))
        order.insert(target - 1, order.pop(source - 1))
        return order
    return permutation(text, count)


def filename(text: str) -> str:
    text = text.replace("{date}", date.today().isoformat())
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "", text).strip(" .")
    if text.lower().endswith(".pdf"):
        text = text[:-4].rstrip(" .")
    text = text[:100] or "document"
    if text.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        text = "_" + text
    return text + ".pdf"


def assemble(parts: list[tuple[bytes, list[int] | None]]) -> bytes:
    writer = PdfWriter()
    for data, pages in parts:
        pdf = reader(data)
        writer.append(pdf, pages=pages, import_outline=False)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def unlock(data: bytes, password: str = "") -> bytes:
    pdf = reader(data, password)
    writer = PdfWriter()
    writer.append(pdf, import_outline=False)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
