"""
Text Splitter Adapter — Decouples LangChain chunking logic from business workers.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

def split_document_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200, separators: list[str] | None = None) -> list[str]:
    """Splits a long text into overlapping chunks suitable for embedding."""
    if separators is None:
        separators = ["\n\n", "\n", " ", ""]
        
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators,
        length_function=len,
        is_separator_regex=False,
    )
    return splitter.split_text(text)
