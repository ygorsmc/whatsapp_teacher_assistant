# Manual do Professor — Assistente WhatsApp

## O que é o Assistente?

O Assistente WhatsApp é um robô que envia **revisões diárias automatizadas** para os seus alunos, com resumos pedagógicos e quizzes de fixação baseados nos materiais do seu Google Drive. Tudo acontece diretamente no WhatsApp — sem aplicativos extras.

---

## Configuração Inicial (uma única vez)

### 1. Compartilhar a Pasta do Google Drive

O robô usa uma Conta de Serviço (Service Account) para acessar os seus arquivos. Ele **não tem acesso automático** às suas pastas.

**Passo a passo:**
1. Abra o Google Drive e localize a pasta raiz onde ficam os materiais dos seus cursos.
2. Clique com o botão direito na pasta → **"Compartilhar"**.
3. Adicione o e-mail da Conta de Serviço como **"Leitor"**:
   ```
   Solicite o e-mail ao administrador do sistema.
   ```
4. Clique em **"Enviar"**.

> ⚠️ Se você revogar o acesso acidentalmente, o sistema não conseguirá ler os materiais. Basta compartilhar novamente.

### 2. Organizar os Materiais por Pastas

Cada pasta de nível superior no seu Drive será tratada como um **curso**. Dentro de cada curso, você deve criar subpastas numeradas para cada **aula**.

```
📁 Sua Pasta Compartilhada
├── 📁 Inteligência Artificial/
│   ├── 📁 1 - Introdução/
│   │   └── apostila_ml.pdf
│   └── 📁 2 - Agentes IA/
│       └── AI_Agents_Apostila.pdf
├── 📁 PIBEP/
│   ├── 📁 1 - Abertura/
│   │   └── aula_01.pptx
│   └── 📁 2 - Tendências/
└── 📊 Cadastro de Alunos (planilha)
```

**Regras do Drive:**
- O nome da pasta de nível superior deve ser **exatamente** o nome do curso na planilha.
- O nome das subpastas deve **começar com um número** (ex: `1 - Introdução`, `02 - Aula dois`). Esse número define a ordem da aula no sistema.
- Formatos suportados: PDF, PPTX (PowerPoint), DOCX (Word), TXT.

### 3. Preencher a Planilha de Cadastro

A planilha é a **fonte de verdade** do sistema. Ela controla quem é aluno, quem é professor, em qual curso cada aluno está e quando ele receberá as revisões.

**Estrutura obrigatória (colunas A até H):**

| Nome | Telefone | Role | Curso | Horário 1 | Horário 2 | Horário 3 | Status |
|------|----------|------|-------|-----------|-----------|-----------|--------|
| Prof. Silva | 5511999998888 | Professor | | | | | Ativo |
| João Santos | 5513991234567 | Aluno | Matemática Básica | 07:00 | 12:00 | 18:00 | Ativo |
| Maria Lima | 5521987654321 | Aluno | Ladrilho Hidráulico | 08:00 | | | Ativo |
| João Santos | 5513991234567 | Aluno | Ladrilho Hidráulico | 19:00 | | | Ativo |

**Regras importantes:**
- **Role:** Escreva `Professor` ou `Aluno`. Professores podem usar os comandos `/` no WhatsApp.
- **Curso:** Deve ser **exatamente** o nome da pasta no Google Drive. Deixe vazio para professores.
- **Horários:** Formato `HH:MM` (ex: `07:00`). São os horários em que o aluno receberá a mensagem de revisão. Até 3 horários por linha.
- **Status:** `Ativo` ou `Inativo`. Alunos inativos param de receber revisões.
- **Aluno em mais de um curso:** Repita a linha do aluno com o novo curso e novos horários (veja o exemplo do João Santos acima).

---

## Comandos do WhatsApp

Envie estes comandos **a partir do seu número de telefone cadastrado como Professor** na planilha.

### `/sinc_alunos`

Lê a planilha e atualiza o banco de dados do sistema.

**Quando usar:**
- Você acabou de adicionar um novo aluno/curso na planilha.
- Você mudou o curso ou o horário de um aluno.
- Você inativou um aluno.

**Variantes aceitas:** `/sync_alunos`, `/sincronizar_alunos`

### `/sinc_materiais`

Lê as pastas do Google Drive, converte os materiais (PPTX/DOCX para PDF) e indexa tudo no banco de vetores para que o robô consiga gerar as revisões e responder dúvidas.

**Quando usar:**
- Você adicionou materiais novos no Drive.
- Você criou um curso novo (lembre-se da *Regra de Ouro* abaixo!).

### `/listar_cursos`

Mostra todos os cursos que o sistema reconheceu para você a partir da planilha.

### `/listar_materiais`

Lê o Google Drive em tempo real e lista quais pastas/arquivos o sistema está enxergando lá dentro. Ótimo para conferir se o robô tem acesso aos seus arquivos.

### `/aula [Turma] = N`

Avança ou retrocede o conteúdo que os alunos podem acessar. 
Exemplo: `/aula Turma 101 = 3`. 
Isso diz ao robô que os alunos daquela turma agora podem receber revisões e tirar dúvidas sobre materiais até a Aula 3.

### `/ajuda`

Mostra a lista de comandos disponíveis.

---

## 🌟 REGRA DE OURO: Como adicionar um Curso Novo

Sempre que criar um curso novo, siga **exatamente** esta ordem para que a ingestão funcione de primeira:

1. **Cadastre na Planilha:** Adicione o curso na planilha e vincule a pelo menos um aluno/turma.
2. **Sincronize Alunos:** Mande `/sinc_alunos` no WhatsApp. Isso ensina ao banco de dados que o curso existe.
3. **Crie no Drive:** Crie a pasta no Drive com o **mesmo nome exato** e coloque as subpastas numeradas com os arquivos.
4. **Sincronize Materiais:** Mande `/sinc_materiais` no WhatsApp. O robô vai ler a pasta, ver que o curso existe no banco, e processar os PDFs!

> ⚠️ **Atenção:** Se você colocar arquivos no Drive e rodar `/sinc_materiais` *antes* de cadastrar na planilha, o robô ignorará os arquivos pois ele "não conhece" aquele curso ainda.

---

## Como Funciona o Dia a Dia

### Para o Aluno

1. No horário agendado, o aluno recebe uma mensagem:
   > *📚 Revisão Diária — Matemática Básica*
   > *Olá João! Preparei uma revisão dos pontos-chave da sua aula atual. Vamos revisar?*
   > `[ ✅ Sim, vamos lá! ]`  `[ ⏰ Depois ]`

2. Ao clicar em "Sim", o robô envia:
   - Um **resumo pedagógico** dos pontos-chave da aula atual.
   - Um **quiz de fixação** com 3 alternativas.

3. O aluno clica na resposta. O robô corrige instantaneamente e explica por quê:
   > *🎉 Correto! [explicação pedagógica]*
   ou
   > *❌ Incorreto. A resposta correta era B. [explicação]*

4. Fora dos horários agendados, o aluno pode enviar **qualquer dúvida** sobre o conteúdo da aula e o robô responde usando o material dos PDFs.

### Para o Professor

### Para o Professor

1. Siga a Regra de Ouro para adicionar cursos.
2. Atualize o ponteiro das turmas enviando `/aula` à medida que o curso avança.
3. O sistema faz o resto automaticamente!

---

## Perguntas Frequentes

### "Posso ter mais de um professor?"
Sim! Basta adicionar outra linha na planilha com `Role = Professor`. Qualquer número cadastrado como Professor poderá usar os comandos `/`.

### "O que acontece se eu digitar o nome do curso errado na planilha?"
O sistema criará um curso com o nome digitado. Se esse nome não corresponder a nenhuma pasta no Drive, o robô não encontrará materiais para gerar as revisões. **Dica:** Use o filtro de pastas (dropdown) do Apps Script na planilha para evitar erros de digitação.

### "Posso mudar o horário de um aluno?"
Sim. Altere o horário na planilha e envie `/sync alunos`. O sistema atualizará automaticamente.

### "E se o aluno mudar de curso?"
Altere o curso na planilha e envie `/sinc_alunos`. O sistema removerá a matrícula antiga e criará a nova automaticamente.

### "O robô responde fora do horário?"
Sim! Se o aluno enviar uma mensagem a qualquer momento, o robô responderá com base no material da aula atual. A diferença é que fora dos horários agendados, o robô não envia a revisão proativamente.
