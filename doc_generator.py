"""
Módulo de Automação de Documentação Técnica

Gera um guia Markdown com marcadores HTML invisíveis por arquivo,
permitindo que o doc_reconstructor.py reconstrua o projeto original.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

# Mapeamento de extensões para syntax highlighting no Markdown.
LANG_MAP: dict[str, str] = {
    "sh":    "bash",
    "yml":   "yaml",
    "env":   "bash",
    "bicep": "bicep",
    "md":    "markdown",
    "txt":   "text",
}

# Arquivos sem extensão que devem ser documentados.
SUPPORTED_NAMES: set[str] = {
    "Dockerfile", "Makefile", ".gitignore", ".amlignore", "Procfile",
}

FILE_MARKER_TEMPLATE = "<!-- FILE_PATH: {path} -->"


class ProjectDocumenter:
    """
    Gera um guia Markdown estruturado e reversível a partir de um diretório.
    """

    def __init__(
        self,
        root_dir: str,
        docs_dir: str = "_docs",
        doc_basename: str = "PROJETO_COMPLETO",
        self_exclude: Optional[list[str]] = None,
    ) -> None:
        """
        Args:
            root_dir:      Diretório raiz do projeto a ser varrido.
            docs_dir:      Nome do subdiretório onde os documentos versionados
                           serão salvos. É automaticamente excluído da
                           documentação e da árvore de diretórios.
            doc_basename:  Prefixo do nome dos arquivos gerados.
                           Ex.: "PROJETO_COMPLETO" → PROJETO_COMPLETO_v1.md
            self_exclude:  Nomes de arquivo adicionais a serem ignorados.
        """
        self.root_dir = Path(root_dir).resolve()
        self.docs_dir = self.root_dir / docs_dir
        self.doc_basename = doc_basename

        self.ignored_patterns: set[str] = {
            "__pycache__", "mlruns", "local_model_output", "mlflow.db",
            docs_dir,                   # exclui o diretório de docs da varredura
        }
        self.supported_extensions: set[str] = {
            ".py", ".sh", ".yaml", ".yml", ".sql", ".bicep", ".env",
            ".txt", ".md",
        }

        # Arquivos que nunca devem ser documentados.
        self._excluded_files: set[str] = {
            "doc_generator.py",
            "doc_reconstructor.py",
            *(self_exclude or []),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_ignore(self, path: Path) -> bool:
        """
        Retorna True se o caminho deve ser ignorado.

        Critérios:
        - Qualquer parte do caminho pertence a ``ignored_patterns``.
        - Qualquer parte do caminho é um diretório cujo nome começa com '.'
          (ex.: .git, .venv, .agents, .pytest_cache).
        """
        return any(
            part in self.ignored_patterns or part.startswith(".")
            for part in path.parts
        )

    def _is_supported(self, file_path: Path) -> bool:
        """
        Retorna True se o arquivo deve ser documentado.

        Aceita tanto por extensão (.py, .sh, .md ...)
        quanto por nome exato (Dockerfile, Makefile ...).
        """
        return (
            file_path.suffix.lower() in self.supported_extensions
            or file_path.name in SUPPORTED_NAMES
        )

    def _get_lang(self, file_path: Path) -> str:
        """Resolve o identificador de linguagem para syntax highlighting."""
        if not file_path.suffix:
            return file_path.name.lower()
        ext = file_path.suffix.lower().lstrip(".")
        return LANG_MAP.get(ext, ext)

    def _read_file(self, file_path: Path) -> str:
        """
        Lê o arquivo tentando UTF-8 primeiro e Latin-1 como fallback.

        Evita que um único arquivo com encoding diferente interrompa
        a geração de todo o guia.
        """
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1")

    def _generate_tree(self) -> str:
        """
        Retorna a árvore de diretórios como string.

        Tenta usar o binário ``tree`` do sistema (saída mais rica).
        Caso não esteja disponível, usa implementação Python nativa.
        """
        ignore_arg = "|".join([*self.ignored_patterns, ".*"])
        try:
            result = subprocess.run(
                ["tree", "-a", "--noreport", "-I", ignore_arg, str(self.root_dir)],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            return self._generate_tree_python()

    def _generate_tree_python(self) -> str:
        """Implementação Python nativa do comando tree."""
        lines: list[str] = [self.root_dir.name]

        def _walk(directory: Path, prefix: str) -> None:
            entries = sorted(
                e for e in directory.iterdir()
                if e.name not in self.ignored_patterns
                and not e.name.startswith(".")
            )
            for i, entry in enumerate(entries):
                connector = "└── " if i == len(entries) - 1 else "├── "
                lines.append(f"{prefix}{connector}{entry.name}")
                if entry.is_dir():
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, prefix + extension)

        _walk(self.root_dir, "")
        return "\n".join(lines)

    def _resolve_versioned_path(self) -> Path:
        """
        Retorna o caminho do próximo arquivo versionado dentro de docs_dir.

        Inspeciona os arquivos existentes no padrão ``<basename>_vN.md``
        e devolve o caminho com N = (maior versão encontrada) + 1.
        Se nenhum arquivo existir, devolve a versão 1.

        Exemplo:
            _docs/ vazio           → _docs/PROJETO_COMPLETO_v1.md
            _docs/ tem v1 e v2    → _docs/PROJETO_COMPLETO_v3.md
        """
        import re

        pattern = re.compile(
            rf"^{re.escape(self.doc_basename)}_v(\d+)\.md$",
            re.IGNORECASE,
        )

        highest = 0
        if self.docs_dir.exists():
            for f in self.docs_dir.iterdir():
                match = pattern.match(f.name)
                if match:
                    highest = max(highest, int(match.group(1)))

        next_version = highest + 1
        filename = f"{self.doc_basename}_v{next_version}.md"
        print(f"  Versão detectada: v{next_version} ({filename})")
        return self.docs_dir / filename

    # ------------------------------------------------------------------
    # Geração
    # ------------------------------------------------------------------

    def generate(self) -> None:
        """Varre o projeto e escreve o guia Markdown versionado em docs_dir."""
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        output_file = self._resolve_versioned_path()

        files_processed = 0
        files_failed = 0

        with output_file.open("w", encoding="utf-8") as f:
            f.write(f"# 📘 Guia Completo do Projeto: `{self.root_dir.name}`\n\n")
            f.write(
                "Este documento foi gerado automaticamente e contém "
                "a estrutura e o código-fonte do projeto.\n\n"
            )
            f.write(
                "> **Nota:** os comentários `<!-- FILE_PATH: ... -->` são "
                "invisíveis neste render, mas permitem reconstruir o projeto "
                "com `doc_reconstructor.py`.\n\n"
            )
            f.write("---\n\n")

            f.write("## 🌳 Estrutura do Projeto\n\n")
            f.write(f"```\n{self._generate_tree()}\n```\n\n")
            f.write("---\n\n")

            for root, dirs, files in os.walk(self.root_dir):
                dirs[:] = sorted(
                    d for d in dirs
                    if d not in self.ignored_patterns and not d.startswith(".")
                )

                current_path = Path(root)
                relative_path = current_path.relative_to(self.root_dir)
                dir_level = min(len(relative_path.parts) + 2, 6)

                if relative_path != Path("."):
                    f.write(f"{'#' * dir_level} 📁 `{relative_path}`\n\n")

                for file in sorted(files):
                    file_path = current_path / file

                    if (
                        self._should_ignore(file_path)
                        or not self._is_supported(file_path)
                        or file_path.name in self._excluded_files
                    ):
                        continue

                    rel_file_path = file_path.relative_to(self.root_dir)
                    file_level = min(dir_level + 1, 6)

                    f.write(
                        FILE_MARKER_TEMPLATE.format(path=rel_file_path.as_posix())
                        + "\n"
                    )
                    f.write(f"{'#' * file_level} 📄 `{rel_file_path}`\n\n")

                    try:
                        content = self._read_file(file_path)
                        lang = self._get_lang(file_path)
                        f.write(f"```{lang}\n{content}\n```\n\n")
                        files_processed += 1
                        print(f"  ✓ {rel_file_path}")
                    except Exception as exc:
                        f.write(f"> ⚠️ Falha de I/O na leitura do arquivo: {exc}\n\n")
                        files_failed += 1
                        print(f"  ✗ {rel_file_path} → {exc}")

        print(f"\n✓ Guia gerado: {output_file}")
        print(f"  Documentados : {files_processed}")
        if files_failed:
            print(f"  Com falha    : {files_failed}")


if __name__ == "__main__":
    documenter = ProjectDocumenter(root_dir=".")
    documenter.generate()