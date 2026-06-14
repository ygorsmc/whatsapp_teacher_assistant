"""
Gerador de chaves de ativação (H2.1 Livro Vivo / H3.1 Professor).

As chaves NÃO são inseridas no banco por aqui: a fonte autoritativa é a aba
"Chaves" da planilha (o escopo do Google é readonly — o bot lê, nunca escreve).
Fluxo de teste:
  1. python scripts/generate_activation_keys.py --curso "Curso X" --turma "Leitores Livro X" --qtd 50 --lote "1a-edicao"
  2. Colar o conteúdo do CSV gerado na aba "Chaves" da planilha
     (colunas: Chave | Curso | Turma | Lote | MaxAtivacoes | Ativa)
  3. /sinc_alunos → as chaves entram em "ActivationKeys"
  4. Gerar os QR Codes a partir do arquivo de links wa.me (1 por linha)

Chaves de professor (onboarding self-service, H3.1): --tipo professor
(prefixo PROF-; Curso/Turma ficam vazios — o professor cria a turma depois).
"""

import argparse
import csv
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

# Alfabeto Crockford base32 (sem 0/O/1/I — à prova de confusão na digitação).
CROCKFORD_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

PREFIXES = {"leitor": "LIVRO", "professor": "PROF"}


def generate_key(prefix: str = "LIVRO") -> str:
    """Gera uma chave no formato PREFIX-XXXX-XXXX (criptograficamente segura)."""
    block = lambda: "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(4))  # noqa: E731
    return f"{prefix}-{block()}-{block()}"


def generate_batch(qty: int, prefix: str = "LIVRO") -> list[str]:
    """Gera `qty` chaves únicas (espaço de 30^8 ≈ 6.5e11 por prefixo)."""
    keys: set[str] = set()
    while len(keys) < qty:
        keys.add(generate_key(prefix))
    return sorted(keys)


def build_wa_link(bot_number: str, key: str) -> str:
    """Deep link wa.me que abre o WhatsApp com a chave pré-preenchida —
    é este link que vira o QR Code impresso no material."""
    message = f"Quero ativar meu material: {key}"
    return f"https://wa.me/{bot_number}?text={quote(message)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera chaves de ativação (CSV p/ planilha + links wa.me p/ QR).")
    parser.add_argument("--qtd", type=int, required=True, help="Quantidade de chaves")
    parser.add_argument("--curso", default="", help="Nome do Curso (igual à pasta do Drive)")
    parser.add_argument("--turma", default="", help="Turma-coorte do material (ex: 'Leitores Livro X')")
    parser.add_argument("--lote", default="lote-1", help="Identificador do lote (revogação em massa)")
    parser.add_argument("--max-ativacoes", type=int, default=1, help="Ativações por chave (anti-pirataria: 1)")
    parser.add_argument("--tipo", choices=["leitor", "professor"], default="leitor")
    parser.add_argument("--numero-bot", default="", help="Número do bot p/ os links wa.me (ex: 5511999998888)")
    parser.add_argument("--saida", default="chaves", help="Prefixo dos arquivos de saída")
    args = parser.parse_args()

    if args.tipo == "leitor" and (not args.curso or not args.turma):
        parser.error("--curso e --turma são obrigatórios para chaves de leitor")
    if args.qtd < 1 or args.qtd > 10000:
        parser.error("--qtd deve estar entre 1 e 10000")

    keys = generate_batch(args.qtd, PREFIXES[args.tipo])

    csv_path = Path(f"{args.saida}.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Chave", "Curso", "Turma", "Lote", "MaxAtivacoes", "Ativa"])
        for key in keys:
            writer.writerow([key, args.curso, args.turma, args.lote, args.max_ativacoes, "TRUE"])

    print(f"✅ {len(keys)} chave(s) geradas em {csv_path} — cole o conteúdo na aba 'Chaves' da planilha.")

    if args.numero_bot:
        links_path = Path(f"{args.saida}_links.txt")
        with links_path.open("w", encoding="utf-8") as f:
            for key in keys:
                f.write(build_wa_link(args.numero_bot, key) + "\n")
        print(f"🔗 Links wa.me (1 por linha, para gerar os QR Codes) em {links_path}.")
    else:
        print("ℹ️  Passe --numero-bot para gerar também os links wa.me dos QR Codes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
