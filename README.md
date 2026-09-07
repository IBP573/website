# Botnest Server

## Importante: não publicar no Netlify

O Netlify serve arquivos estáticos. Este projeto precisa executar `app.py`, aceitar `/api/bots` e manter processos Python/Node. No Netlify, o site pode mostrar 404 ou `Failed to fetch` porque o Flask não está rodando.

## Deploy recomendado no Render

1. Extraia este projeto ou envie a pasta para um repositório GitHub.
2. No Render, escolha **New + Web Service** e selecione o repositório.
3. Runtime: **Python**.
4. Build command: `pip install -r requirements.txt`.
5. Start command: `gunicorn app:app --workers 1 --threads 4 --timeout 0`.
6. Publique e abra a URL do Render. A página correta será a URL raiz `/`, não `/static/index.html`.

O arquivo `render.yaml` já contém essa configuração.

## Rodar localmente

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PORT=8081 python3 app.py
```

Abra `http://localhost:8081`.

## O que faz

Aceita ZIPs, salva cada projeto, detecta Python ou Node.js, procura `bot.py`, `main.py`, `bot.js`, `index.js` e `package.json`, registra User/ID/comandos, mostra logs e controla processos com iniciar, parar e reiniciar. O limite é de 10 bots e 100 MB por upload.

## Atenção sobre 24 horas

O plano gratuito de várias plataformas pode dormir ou limitar processos. Para bots 24h reais, use um plano persistente e um servidor isolado. Este programa executa código enviado ao servidor: use usuário sem privilégios, firewall e isolamento adicional antes de expor na internet. Nunca coloque tokens no HTML ou nos logs.
