from flask import Flask, jsonify, request, send_from_directory
from pathlib import Path
import json, os, re, shutil, signal, subprocess, threading, time, zipfile

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PROJECTS = DATA / "projects"
DB_FILE = DATA / "bots.json"
PROJECTS.mkdir(parents=True, exist_ok=True)
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
lock = threading.Lock()
running = {}

def load_db():
    if not DB_FILE.exists(): return []
    try: return json.loads(DB_FILE.read_text())
    except Exception: return []

def save_db(rows):
    DB_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2))

def files_for(folder):
    return [p for p in folder.rglob("*") if p.is_file() and p.stat().st_size <= 2_000_000]

def inspect_project(folder):
    files = files_for(folder)
    texts = []
    for p in files:
        if p.suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".env", ".txt"}:
            try: texts.append((p.name, p.read_text(errors="ignore")))
            except Exception: pass
    joined = "\n".join(t for _, t in texts)
    names = [p.name.lower() for p in files]
    runtime = "python" if any(n.endswith(".py") for n in names) or re.search(r"discord\.py|from discord import", joined, re.I) else "node"
    user = "Não identificado"
    m = re.search(r"(?:BOT_NAME|BOT_USERNAME|BOT_USER|CLIENT_NAME)\s*=\s*[\"']([^\"']+)", joined, re.I)
    if m: user = m.group(1)
    bot_id = "Não identificado"
    m = re.search(r"(?:BOT_ID|CLIENT_ID|APPLICATION_ID|APPLICATIONID|USER_ID)\s*=\s*[\"']?(\d{5,25})", joined, re.I) or re.search(r"(?:clientId|applicationId|userId)\s*[:=]\s*[\"'](\d{5,25})", joined, re.I)
    if m: bot_id = m.group(1)
    commands = set()
    patterns = [r"@bot\.command\s*\(\s*(?:name\s*=\s*)?[\"']([^\"']+)", r"commands\.command\s*\(\s*[\"']([^\"']+)", r"setName\(\s*[\"']([^\"']+)", r"(?:command|name)\s*[:=]\s*[\"']([a-zA-Z0-9_-]+)[\"']"]
    for pat in patterns:
        commands.update(m.group(1).lstrip("/") for m in re.finditer(pat, joined, re.I))
    return {"runtime": runtime, "user": user, "botId": bot_id, "commands": sorted("/" + c for c in commands if c)[:80], "files": [str(p.relative_to(folder)) for p in files][:150]}

def add_log(bot, message, level="info"):
    bot.setdefault("logs", []).insert(0, {"time": time.time(), "message": message, "level": level})
    bot["logs"] = bot["logs"][:300]

def choose_entry(folder, runtime):
    if runtime == "python":
        for name in ("bot.py", "main.py", "index.py", "app.py"):
            if (folder / name).exists(): return ["python3", name]
        found = next(folder.glob("*.py"), None)
        return ["python3", found.name] if found else None
    pkg = folder / "package.json"
    if pkg.exists():
        try:
            main = json.loads(pkg.read_text()).get("main")
            if main and (folder / main).exists(): return ["node", main]
        except Exception: pass
    for name in ("bot.js", "index.js", "main.js", "app.js"):
        if (folder / name).exists(): return ["node", name]
    found = next(folder.glob("*.js"), None)
    return ["node", found.name] if found else None

def watcher(bot_id, proc):
    for line in iter(proc.stdout.readline, ""):
        if line:
            with lock:
                rows = load_db(); bot = next((b for b in rows if b["id"] == bot_id), None)
                if bot:
                    add_log(bot, line.rstrip(), "error" if "error" in line.lower() or "traceback" in line.lower() else "info")
                    save_db(rows)
    with lock:
        rows = load_db(); bot = next((b for b in rows if b["id"] == bot_id), None)
        if bot and bot.get("status") == "online":
            bot["status"] = "offline"; add_log(bot, f"Processo encerrado (código {proc.poll()}).", "warn"); save_db(rows)
        running.pop(bot_id, None)

def start_bot(bot):
    folder = PROJECTS / str(bot["id"]); command = choose_entry(folder, bot["runtime"])
    if not command: raise RuntimeError("Não encontrei bot.py/main.py ou bot.js/index.js.")
    env = os.environ.copy(); env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(command, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env, start_new_session=True)
    running[bot["id"]] = proc; threading.Thread(target=watcher, args=(bot["id"], proc), daemon=True).start()
    return command

@app.get("/")
def home(): return send_from_directory(ROOT / "static", "index.html")
@app.get("/api/bots")
def list_bots(): return jsonify(load_db())
@app.post("/api/bots")
def upload_bot():
    if len(load_db()) >= 10: return jsonify(error="Limite de 10 bots atingido."), 400
    upload = request.files.get("file")
    if not upload or not upload.filename.lower().endswith(".zip"): return jsonify(error="Envie um arquivo ZIP."), 400
    bot_id = int(time.time() * 1000); folder = PROJECTS / str(bot_id); folder.mkdir()
    archive = folder / "upload.zip"; upload.save(archive)
    try:
        with zipfile.ZipFile(archive) as z: z.extractall(folder / "src")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True); return jsonify(error="ZIP inválido."), 400
    source = folder / "src"; candidates = list(source.iterdir()) if source.exists() else []
    project_root = candidates[0] if len(candidates) == 1 and candidates[0].is_dir() else source
    analysis = inspect_project(project_root); archive.unlink(missing_ok=True)
    bot = {"id": bot_id, "name": request.form.get("name") or upload.filename.rsplit(".", 1)[0], "file": upload.filename, "runtime": analysis["runtime"], "status": "offline", "analysis": analysis, "logs": [], "created": time.time()}
    add_log(bot, f"Pasta analisada: runtime {analysis['runtime']}; {len(analysis['commands'])} comando(s).")
    add_log(bot, f"User: {analysis['user']} | ID: {analysis['botId']}")
    rows = load_db(); rows.insert(0, bot); save_db(rows); return jsonify(bot), 201

@app.get("/api/bots/<int:bot_id>/logs")
def logs(bot_id):
    bot = next((b for b in load_db() if b["id"] == bot_id), None)
    return jsonify(bot.get("logs", []) if bot else []), 404 if not bot else 200

@app.post("/api/bots/<int:bot_id>/<action>")
def action(bot_id, action):
    rows = load_db(); bot = next((b for b in rows if b["id"] == bot_id), None)
    if not bot: return jsonify(error="Bot não encontrado."), 404
    try:
        if action == "start":
            if bot_id not in running:
                command = start_bot(bot); bot["status"] = "online"; add_log(bot, "Processo iniciado. Bot online.", "success"); add_log(bot, f"User: {bot['analysis']['user']}"); add_log(bot, f"ID: {bot['analysis']['botId']}"); add_log(bot, f"Runtime: {bot['runtime']}"); [add_log(bot, f"CMD: {c}", "success") for c in bot["analysis"]["commands"]]
        elif action == "stop":
            proc = running.pop(bot_id, None)
            if proc: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            bot["status"] = "offline"; add_log(bot, "Processo desligado pelo painel.", "warn")
        elif action == "restart":
            proc = running.pop(bot_id, None)
            if proc: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            bot["status"] = "restarting"; add_log(bot, "Reiniciando processo...")
            command = start_bot(bot); bot["status"] = "online"; add_log(bot, "Processo reiniciado. Bot online.", "success")
        else: return jsonify(error="Ação inválida."), 400
        save_db(rows); return jsonify(bot)
    except Exception as e:
        bot["status"] = "error"; add_log(bot, str(e), "error"); save_db(rows); return jsonify(error=str(e), bot=bot), 400

@app.delete("/api/bots/<int:bot_id>")
def delete_bot(bot_id):
    global running
    proc = running.pop(bot_id, None)
    if proc:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError: pass
    rows = [b for b in load_db() if b["id"] != bot_id]; shutil.rmtree(PROJECTS / str(bot_id), ignore_errors=True); save_db(rows); return jsonify(ok=True)

if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threaded=True)
