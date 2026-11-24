# Spec — Gravador e Transcritor de Reuniões (Windows)

## Objetivo

Um executável pequeno para Windows que fica numa janela flutuante na tela. Com um clique, ele começa a capturar o áudio da reunião ou chamada (Teams ou qualquer outro app), transcreve localmente com Whisper e, ao parar, gera um resumo com a lista de tarefas. Tudo gratuito e rodando na máquina, sem bot na chamada.

## Escopo

- Funciona em reuniões agendadas e em chamadas de voz diretas, porque captura o áudio do sistema e não depende de link de reunião.
- Funciona com Teams gratuito e com qualquer outro app de chamada.
- Fora do escopo na v1: identificar vários participantes pelo nome, gravar vídeo, sincronizar na nuvem.

## Requisitos funcionais

1. **Janela flutuante**: janela pequena, sempre no topo, que dá para arrastar, com os botões Iniciar e Parar, um cronômetro e um indicador de nível de áudio.
2. **Captura de áudio**: dois fluxos gravados ao mesmo tempo, em canais separados.
   - Microfone, que é a voz do Matheus.
   - Saída do sistema via WASAPI loopback, que é a voz do outro lado.
3. **Transcrição**: transcrição em pedaços de cerca de 30 segundos durante a chamada, para que o texto fique pronto quase ao mesmo tempo que a reunião acaba. O idioma padrão é o português.
4. **Rótulos de quem fala**: cada trecho é marcado como "Eu" (microfone) ou "Outro" (sistema), com o horário.
5. **Resumo e tarefas**: ao parar, o texto completo é enviado a um LLM, que devolve um resumo, as decisões tomadas, as minhas tarefas, as tarefas do outro lado e os prazos citados.
6. **Saída**: uma pasta por reunião com o áudio (.wav ou .opus), a transcrição (.md) e o resumo com tarefas (.md). Botão para abrir a pasta.
7. **Aviso de gravação**: lembrete na tela, ao iniciar, para avisar os participantes (LGPD).

## Requisitos não funcionais

- Transcrição 100% local e gratuita. O único custo opcional é o LLM do resumo.
- Roda em CPU comum. Se houver GPU NVIDIA, usa CUDA automaticamente.
- Uso leve de memória durante a chamada, para não travar o Teams.
- Instalação em um único .exe, sem precisar instalar Python.

## Stack sugerida

- **Linguagem**: Python 3.11+.
- **Captura**: PyAudioWPatch (WASAPI loopback e microfone).
- **Transcrição**: faster-whisper, com o modelo `small` ou `medium` e quantização int8 na CPU.
- **Interface**: PySide6 ou CustomTkinter para a janela flutuante.
- **Resumo**: API do Claude ou da OpenAI, configurável. Alternativa gratuita: Ollama com um modelo local.
- **Empacotamento**: PyInstaller, gerando um .exe.

## Arquitetura

- **Thread de captura**: lê o microfone e o loopback, reamostra para 16 kHz mono por canal e coloca os blocos numa fila.
- **Thread de transcrição**: consome a fila em blocos de cerca de 30 segundos, roda o faster-whisper com filtro de voz (VAD) e grava os trechos com rótulo e horário.
- **Pós-processamento**: ao parar, junta os trechos em ordem cronológica, monta a transcrição final e chama o LLM com o prompt de tarefas.
- **Configurações** (arquivo config.json): dispositivo de microfone, dispositivo de saída, modelo do Whisper, idioma, provedor do LLM e chave de API, pasta de saída.

## Prompt de tarefas (base)

"Você recebe a transcrição de uma reunião. 'Eu' é o Matheus e 'Outro' é o interlocutor. Devolva em Markdown: um resumo em até 5 linhas, as decisões tomadas, as tarefas do Matheus (com prazo, se citado), as tarefas do outro lado e os pontos em aberto."

## Fases de entrega

1. **Fase 1**: script de linha de comando que grava o microfone e o loopback em dois arquivos .wav.
2. **Fase 2**: transcrição com faster-whisper e rótulos Eu/Outro, gerando o .md.
3. **Fase 3**: resumo e tarefas via LLM.
4. **Fase 4**: janela flutuante com Iniciar e Parar, cronômetro e indicador de nível.
5. **Fase 5**: empacotamento em .exe com PyInstaller e tela de configurações.

## Critérios de aceite

- Grava e transcreve uma chamada de voz do Teams gratuito sem bot na chamada.
- Uma reunião de 30 minutos gera transcrição e tarefas em menos de 2 minutos depois de parar (CPU comum, modelo small).
- O .exe abre em uma máquina Windows sem Python instalado.

## Riscos

- Fones Bluetooth às vezes mudam de dispositivo no meio da chamada. Mitigação: detectar a troca e reconectar.
- Eco do alto-falante entrando no microfone. Mitigação: recomendar fone de ouvido.
- Precisão do Whisper em conversas com sotaque ou muito ruído. Mitigação: permitir trocar para o modelo medium.
