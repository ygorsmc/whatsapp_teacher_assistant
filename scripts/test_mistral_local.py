import sys
import os
# Adiciona a raiz do projeto ao PYTHONPATH para a IDE conseguir achar a pasta 'src'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import base64
import argparse
from mistralai.client import Mistral
from dotenv import load_dotenv

# Importa a lógica de fatiamento já existente no seu projeto
from src.parsing.pdf_utils import split_pdf_for_ocr

# Carrega as variáveis de ambiente locais (como a MISTRAL_API_KEY)
load_dotenv(".env.local")

async def test_mistral_local(pdf_path: str, output_md: str, images_dir: str):
    os.makedirs(images_dir, exist_ok=True)
    print(f"Diretório de imagens: {images_dir}")
    mistral_key = os.getenv("MISTRAL_API_KEY")
    if not mistral_key:
        print("Erro: MISTRAL_API_KEY não configurada no .env.local")
        return

    client = Mistral(api_key=mistral_key)
    
    print(f"Lendo o arquivo local: {pdf_path}")
    try:
        with open(pdf_path, "rb") as f:
            file_bytes = f.read()
    except FileNotFoundError:
        print(f"Erro: Arquivo '{pdf_path}' não encontrado.")
        return
    
    print(f"Tamanho do arquivo: {len(file_bytes)/1024/1024:.2f} MB")
    
    # Reutiliza a lógica de fatiamento do seu MistralOCRParser
    chunks = split_pdf_for_ocr(file_bytes)
    if len(chunks) > 1:
        print(f"PDF grande detectado. Fatiando em {len(chunks)} pedaços para o OCR...")
    
    raw_paragraphs = []
    page_offset = 0
    
    for chunk_idx, chunk_bytes in enumerate(chunks):
        base64_pdf = base64.b64encode(chunk_bytes).decode("utf-8")
        print(f"Enviando pedaço {chunk_idx + 1}/{len(chunks)} para a Mistral API...")
        
        try:
            ocr_response = await client.ocr.process_async(
                model="mistral-ocr-latest",
                document={
                    "type": "document_url",
                    "document_url": f"data:application/pdf;base64,{base64_pdf}",
                },
                include_image_base64=True, # Solicitado para salvar as imagens localmente
                extract_header=True,
                extract_footer=True,
            )
        except Exception as e:
            print(f"Falha no Mistral OCR para o pedaço {chunk_idx + 1}: {e}")
            return
            
        print(f"Extraindo páginas do pedaço {chunk_idx + 1}...")
        
        # Pula o enriquecimento do Gemini, mas salva as imagens retornadas pela Mistral API
        for i, page in enumerate(ocr_response.pages):
            page_md = getattr(page, "markdown", "") or ""
            raw_paragraphs.append(f"## Página {page_offset + i + 1}\n\n{page_md}")
            
            # Extrai e salva as imagens base64
            for img in getattr(page, "images", []) or []:
                if getattr(img, "image_base64", None):
                    # O base64 da Mistral pode vir puro ou com prefixo data:image/jpeg;base64,
                    img_data = img.image_base64
                    if "," in img_data:
                        img_data = img_data.split(",", 1)[1]
                    
                    try:
                        img_bytes = base64.b64decode(img_data)
                        img_filename = img.id if getattr(img, "id", None) else f"img_pag_{page_offset + i + 1}.jpeg"
                        img_path = os.path.join(images_dir, img_filename)
                        
                        with open(img_path, "wb") as f_img:
                            f_img.write(img_bytes)
                    except Exception as e:
                        print(f"Erro ao salvar imagem {getattr(img, 'id', 'desconhecida')}: {e}")
            
        page_offset += len(ocr_response.pages)
        
    full_markdown = "\n\n".join(raw_paragraphs)
    
    print(f"Salvando resultado final em: {output_md}")
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(full_markdown)
        
    print(f"Processo concluído! {page_offset} páginas extraídas e salvas no .md com sucesso.")

if __name__ == "__main__":
    # Caminhos fixos para facilitar o teste diretamente pela IDE (sem precisar usar o terminal)
    pdf_fixo = "scripts/Practical_English_Usage.pdf"
    saida_md = "resultado_mistral.md"
    pasta_imagens = "imagens_extraidas"
    
    asyncio.run(test_mistral_local(
        pdf_path=pdf_fixo, 
        output_md=saida_md, 
        images_dir=pasta_imagens
    ))
