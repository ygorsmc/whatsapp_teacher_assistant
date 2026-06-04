from src.shared.config import settings
from src.parsing.interfaces import IDocumentParser

_parser_instance = None

def get_parser() -> IDocumentParser:
    global _parser_instance
    if _parser_instance is None:
        if settings.PARSER_BACKEND.lower() == "local":
            from src.parsing.local_parser import LocalPDFParser
            _parser_instance = LocalPDFParser()
        else:
            from src.parsing.azure_parser import AzureDocIntelParser
            _parser_instance = AzureDocIntelParser()
    return _parser_instance
