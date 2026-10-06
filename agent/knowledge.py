"""Доступ к `knowledge/`: разбор разделов, поиск и безопасное чтение файлов."""

import re
from dataclasses import dataclass

import config

ALLOWED_SUFFIXES = {".md", ".txt"}
MAX_FILE_CHARS = 8000


class KnowledgeAccessError(Exception):
    pass


@dataclass(frozen=True)
class Section:
    file: str
    title: str
    body: str


def _root():
    return config.KNOWLEDGE_DIR.resolve()


def list_files() -> list[str]:
    root = _root()
    names = []
    for p in sorted(root.rglob("*")):
        if p.suffix in ALLOWED_SUFFIXES and p.is_file() and not p.is_symlink():
            rel = p.relative_to(root)
            if not any(part.startswith(".") for part in rel.parts):
                names.append(rel.as_posix())
    return names


def safe_path(name: object):
    """Разрешает путь только внутри `knowledge/`; иначе KnowledgeAccessError."""
    if not isinstance(name, str) or not name or len(name) > 100:
        raise KnowledgeAccessError("Недопустимое имя файла.")
    if "\x00" in name or "\\" in name or ".." in name or name.startswith(("/", ".", "~")):
        raise KnowledgeAccessError("Недопустимое имя файла.")
    root = _root()
    candidate = root / name
    if candidate.is_symlink():
        raise KnowledgeAccessError("Недопустимое имя файла.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise KnowledgeAccessError("Файл вне базы знаний.")
    if any(part.startswith(".") for part in resolved.relative_to(root).parts):
        raise KnowledgeAccessError("Недопустимое имя файла.")
    if resolved.suffix not in ALLOWED_SUFFIXES or not resolved.is_file():
        raise KnowledgeAccessError("Файл не найден в базе знаний.")
    return resolved


def read_file(name: object) -> str:
    return safe_path(name).read_text(encoding="utf-8")[:MAX_FILE_CHARS]


def load_sections(only: str | None = None) -> list[Section]:
    """Делит файлы на разделы по заголовкам `## `."""
    sections: list[Section] = []
    for name in list_files():
        if only and name != only:
            continue
        title, lines = "", []
        text = (_root() / name).read_text(encoding="utf-8")

        def flush():
            body = "\n".join(lines).strip()
            if title and body:
                sections.append(Section(name, title, body))

        for line in text.splitlines():
            if line.startswith("## "):
                flush()
                title, lines = line[3:].strip(), []
            else:
                lines.append(line)
        flush()
    return sections


def _tokens(text: str) -> set[str]:
    # Грубый «стемминг» префиксом, чтобы «заявка»/«заявку» совпадали.
    return {w[:5] for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def search(query: str, limit: int = 3) -> list[tuple[Section, int]]:
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for sec in load_sections():
        score = 2 * len(q & _tokens(sec.title)) + len(q & _tokens(sec.body))
        if score:
            scored.append((sec, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]
