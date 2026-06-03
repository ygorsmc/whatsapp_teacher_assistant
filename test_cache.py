import asyncio
import os
from dotenv import load_dotenv

# Load local env vars (which point to local db, in-memory redis fallback, etc)
load_dotenv()

from src.rag.review_generator import generate_review
from src.rag.quiz_generator import generate_quiz
import src.rag.review_generator as review_module

# Mock retrieve_context so we don't need real Qdrant data
async def fake_retrieve_context(*args, **kwargs):
    return "O Scrum é um framework ágil para gestão e planejamento de projetos de software. Numa sprint do Scrum temos o Product Owner, Scrum Master e os Developers. Reuniões diárias (Daily) duram 15 minutos. A Sprint Review serve para inspecionar o incremento. A retrospectiva busca melhoria contínua do time."

review_module.retrieve_context = fake_retrieve_context

from src.cache.topic_cache import get_cached_topics

async def main():
    print("\n" + "="*50)
    print("TESTE 1: REVISÃO AGENDADA (generate_review)")
    print("="*50)
    
    course_id = "test_course_id"
    lesson_num = 1
    
    print(f"Chamada 1: Esperamos CACHE MISS (Primeiro aluno a gerar a revisão da aula {lesson_num})")
    print("Aguardando LLM extrair os tópicos...")
    
    review1 = await generate_review(
        teacher_id="test_teacher",
        teacher_name="Ygor",
        teacher_subject="IA",
        student_name="João",
        course_id=course_id,
        course_name="Curso Teste",
        current_lesson_number=lesson_num,
        lesson_label="Aula Teste",
    )
    
    if review1:
        print("\n✅ Revisão 1 Gerada!")
        print(f"Resumo: {review1.summary[:100]}...")
        print(f"Quiz Pergunta: {review1.quiz.question}")
    
    print("\nVerificando se os tópicos foram pro cache...")
    cached = await get_cached_topics(course_id, lesson_num)
    if cached:
        print(f"✅ Tópicos salvos no cache: {cached}")
    else:
        print("❌ Tópicos não encontrados no cache!")
        
    print(f"\nChamada 2: Esperamos CACHE HIT (Segundo aluno gerando revisão da MESMA aula)")
    
    review2 = await generate_review(
        teacher_id="test_teacher",
        teacher_name="Ygor",
        teacher_subject="IA",
        student_name="Maria",
        course_id=course_id,
        course_name="Curso Teste",
        current_lesson_number=lesson_num,
        lesson_label="Aula Teste",
    )
    
    if review2:
        print("\n✅ Revisão 2 Gerada!")
        print(f"Resumo: {review2.summary[:100]}...")
        print(f"Quiz Pergunta: {review2.quiz.question}")
        
    print("\nVariância da Revisão:")
    if review1 and review2 and review1.summary != review2.summary:
        print("✅ SUCESSO: Os resumos são diferentes (Perspectivas diferentes aplicadas!)")
    else:
        print("❌ FALHA: Os resumos ficaram iguais ou algo deu errado.")
        

    print("\n" + "="*50)
    print("TESTE 2: COMANDO /QUIZ (generate_quiz)")
    print("="*50)
    
    course_id2 = "test_course_id_2"
    lesson_num2 = 2
    
    # Retrieve some fake context just to test
    lesson_content = "Nesta aula 2 de IA, abordamos redes neurais profundas, backpropagation, gradiente descendente estocástico e overfitting."
    
    print("Chamada 1: Esperamos CACHE MISS")
    quiz1 = await generate_quiz(course_id2, lesson_num2, lesson_content)
    
    if quiz1:
        print(f"✅ Quiz 1 Gerado: {quiz1.question}")
        
    cached2 = await get_cached_topics(course_id2, lesson_num2)
    print(f"Tópicos em cache: {cached2}")
    
    print("\nChamada 2: Esperamos CACHE HIT (O professor digitou /quiz de novo)")
    quiz2 = await generate_quiz(course_id2, lesson_num2, lesson_content)
    
    if quiz2:
        print(f"✅ Quiz 2 Gerado: {quiz2.question}")
        
    if quiz1 and quiz2 and quiz1.question != quiz2.question:
        print("✅ SUCESSO: Os quizzes são diferentes!")
        
if __name__ == "__main__":
    asyncio.run(main())
