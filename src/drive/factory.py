"""
Factory das fontes de material e de cadastro — troca por env var, sem tocar na
lógica de negócio (mesmo padrão de `parsing/factory.py`, `llm/factory.py`…).

  MATERIAL_SOURCE_BACKEND = gdrive (default) | local | ...
  REGISTRY_BACKEND        = gdrive (default) | local_csv | ...

Default = `gdrive` ⇒ comportamento atual byte a byte. Os adapters são importados
de forma preguiçosa: usar o backend `local` NÃO importa o cliente Google (e
vice-versa), evitando inicialização desnecessária de SDK/credenciais.
"""

from src.shared.config import settings
from src.drive.interfaces import IMaterialSource, IRegistrySource

_material_instance: IMaterialSource | None = None
_registry_instance: IRegistrySource | None = None


def get_material_source() -> IMaterialSource:
    """Fonte ativa dos ARQUIVOS de material (cacheada por processo)."""
    global _material_instance
    if _material_instance is None:
        backend = settings.MATERIAL_SOURCE_BACKEND.lower()
        if backend == "local":
            from src.drive.local import LocalFolderMaterialSource
            _material_instance = LocalFolderMaterialSource()
        else:  # "gdrive" (default)
            from src.drive.client import drive_client
            _material_instance = drive_client
    return _material_instance


def get_registry_source() -> IRegistrySource:
    """Fonte ativa do CADASTRO tabular (cacheada por processo)."""
    global _registry_instance
    if _registry_instance is None:
        backend = settings.REGISTRY_BACKEND.lower()
        if backend in ("local_csv", "local", "csv"):
            from src.drive.local import LocalCsvRegistrySource
            _registry_instance = LocalCsvRegistrySource()
        else:  # "gdrive" (default)
            from src.drive.client import drive_client
            _registry_instance = drive_client
    return _registry_instance


def reset_source_cache() -> None:
    """Limpa as instâncias cacheadas (usado em testes ao alternar backend)."""
    global _material_instance, _registry_instance
    _material_instance = None
    _registry_instance = None
