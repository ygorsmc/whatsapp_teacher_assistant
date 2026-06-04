from abc import ABC, abstractmethod
from typing import List

class IDocumentParser(ABC):
    """Abstract interface for parsing documents into markdown blocks."""
    
    @abstractmethod
    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        """
        Parses a document (e.g. PDF) into a list of markdown string blocks.
        
        Args:
            blob_url: The original URL of the document.
            file_bytes: The downloaded raw bytes of the document.
            filename: The name of the file.
            
        Returns:
            A list of strings, where each string is a semantic block (e.g., a markdown paragraph or section).
        """
        pass
