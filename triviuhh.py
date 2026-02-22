#!/usr/bin/env python3
"""
triviuhh.py — Triviuhh production server
HTTP + WebSocket on a single port via aiohttp.

Deploy to Render.com (or Railway / Fly.io):
  - Set PORT env var (platform does this automatically)
  - Render sets RENDER_EXTERNAL_URL automatically → correct wss:// URL for players
  - Local fallback: auto-detects LAN IP
"""

import asyncio
import csv
import json
import os
import random
import socket
import sys
import time

import aiohttp
from aiohttp import web
import pyqrcode
from unidecode import unidecode
import difflib


# ── Helpers ───────────────────────────────────────────────────────────────────────────────────

def unidecode_allcaps_shorten32(s):
    tmp = unidecode(s)
    return tmp[:min(len(tmp), 32)].upper()


def is_too_close(lie, answer):
    """Return True if lie is too similar to the real answer (both already normalised)."""
    if not lie or not answer:
        return False
    # Substring in either direction (catches RICHTER ⊂ RICHTER SCALE)
    if lie in answer or answer in lie:
        return True
    # Word-set overlap ≥80 %
    lie_words = set(lie.split())
    ans_words = set(answer.split())
    if lie_words and ans_words:
        overlap = len(lie_words & ans_words)
        if overlap / min(len(lie_words), len(ans_words)) >= 0.8:
            return True
    # Character-level similarity ≥75 %
    if difflib.SequenceMatcher(None, lie, answer).ratio() >= 0.75:
        return True
    return False


# ── Game data classes ─────────────────────────────────────────────────────────

class Question:
    def __init__(self, question, answer, author=None, flavor=None):
        self.question = question
        self.answer = answer
        self.author = author
        self.flavor = flavor
        self.lies = {}     # name → lie text
        self.choices = {}  # name → chosen answer
        self.likes = {}    # name → liked answer

    def __repr__(self):
        return json.dumps({'question': self.question, 'answer': self.answer})

    def remove_player(self, name):
        self.lies.pop(name, None)
        self.choices.pop(name, None)
        self.likes.pop(name, None)

    def get_player_info(self, name):
        return {
            'lie':    self.lies.get(name),
            'choice': self.choices.get(name),
            'likes':  self.likes.get(name),
        }

    def get_scoreorder(self):
        scoreorder = []
        seen = set()
        for lier, lie in self.lies.items():
            count = sum(1 for s, c in self.choices.items() if c == lie and s != lier)
            t = (lie, count)
            if count > 0 and t not in seen:
                seen.add(t)
                scoreorder.append(t)
        scoreorder.sort(key=lambda x: x[1], reverse=True)
        correct = sum(1 for c in self.choices.values() if c == self.answer)
        scoreorder.append((self.answer, correct))
        return scoreorder


class Player:
    def __init__(self, name):
        self.name = name
        self.score = 0
        self.likecount = 0

    def __repr__(self):
        return f'{self.name} ({self.score}pt)'

    def reset(self):
        self.score = 0
        self.likecount = 0

    def get_info(self):
        return {'name': self.name, 'score': self.score, 'likecount': self.likecount}


# ── Game ──────────────────────────────────────────────────────────────────────

class Game:
    def __init__(self):
        self.states = ['pregame', 'lietome', 'lieselection', 'scoring', 'finalscoring']
        self.state = 'pregame'

        self.clients = []
        self.viewers = []
        self.players = {}               # ws → Player
        self.disconnected_players = {}  # name → Player

        self.questions = []
        self.cur_question = None
        self.currentlie = None
        self.scoreorder = []

        self.forcestart = False
        self.paused = False
        self.roundcount = 0
        self.questionsfilename = 'questions2.csv'
        self.t = time.time()

        # Timings (seconds)
        self.questionsperround = 15
        self.lietime   = 60
        self.choicetime = 30
        self.scoretime  = 20

    def time(self):
        self.t = time.time()

    # ── Players ──────────────────────────────────────────────────────────────

    def add_player(self, client, name):
        name = name.strip()[:32]
        if not name:
            return False
        if name in [p.name for p in self.players.values()]:
            print(f'Duplicate login attempt: {name}')
            return False
        if name in self.disconnected_players:
            self.players[client] = self.disconnected_players.pop(name)
            print(f'{name} reconnected')
        else:
            self.players[client] = Player(name)
            print(f'{name} joined')
        return True

    def remove_player(self, client):
        self.clients.discard(client) if hasattr(self.clients, 'discard') else (
            self.clients.remove(client) if client in self.clients else None)
        if client in self.viewers:
            self.viewers.remove(client)
        if client in self.players:
            player = self.players.pop(client)
            if player.score > 0 or player.likecount > 0:
                self.disconnected_players[player.name] = player
            if self.cur_question:
                self.cur_question.remove_player(player.name)
            if not self.players:
                print('Last player left — returning to pregame')
                self.state = 'pregame'

    def get_player_by_name(self, name):
        return next((p for p in self.players.values() if p.name == name), None)

    # ── Questions ─────────────────────────────────────────────────────────────

    def load_questions(self, filename=None):
        if filename:
            self.questionsfilename = filename
        with open(self.questionsfilename, 'r', encoding='utf-8') as f:
            first = f.readline()
            f.seek(0)
            is_csv = (self.questionsfilename.lower().endswith('.csv') or
                      (',' in first and 'Question' in first))
            if is_csv:
                for row in csv.DictReader(f):
                    q = row.get('Question') or row.get('question')
                    a = row.get('Answer') or row.get('answer')
                    if q and a:
                        self.questions.append(Question(
                            q, unidecode_allcaps_shorten32(a),
                            author=row.get('Category') or None,
                            flavor=row.get('Sub_Category') or None,
                        ))
            else:
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        self.questions.append(Question(
                            parts[0], unidecode_allcaps_shorten32(parts[1]),
                            author=parts[2] if len(parts) > 2 else None,
                            flavor=parts[3] if len(parts) > 3 else None,
                        ))
        random.shuffle(self.questions)
        self.questionsperround = min(self.questionsperround, len(self.questions))
        print(f'Loaded {len(self.questions)} questions')

    def load_next_question(self):
        self.time()
        if not self.questions:
            self.reset()
        self.cur_question = self.questions.pop(0)
        self.roundcount += 1
        self.currentlie = None
        self.scoreorder = []
        print(f'Question: {self.cur_question}')

    def reset(self):
        self.questions = []
        self.load_questions()
        for p in self.players.values():
            p.reset()
        self.roundcount = 0

    # ── Scoring ───────────────────────────────────────────────────────────────

    def do_scoring(self):
        """Calculate and apply scores, then signal full reveal."""
        for chooser_name, choice in self.cur_question.choices.items():
            chooser = self.get_player_by_name(chooser_name)
            if choice == self.cur_question.answer:
                if chooser:
                    chooser.score += 1
                print(f'{chooser_name} guessed correctly')
            for lier_name, lie in self.cur_question.lies.items():
                if lie == choice and lier_name != chooser_name:
                    lier = self.get_player_by_name(lier_name)
                    if lier:
                        lier.score += 1
                    print(f'{lier_name} fooled {chooser_name}')
        self.scoreorder = self.cur_question.get_scoreorder()
        self.currentlie = None  # show all answers at once in the reveal

    def lie_selection_received(self, client, selected):
        if self.state != 'lieselection':
            return False
        if client not in self.players:
            return False
        player = self.players[client]
        if player.name in self.cur_question.choices:
            return False
        if self.cur_question.lies.get(player.name) == selected:
            print(f'{player.name} tried to vote for their own lie')
            return False
        self.cur_question.choices[player.name] = selected
        print(f'{player.name} voted for: {selected}')
        return True

    def like_recieved(self, client, liked):
        if self.state != 'lieselection':
            return False
        if client not in self.players:
            return False
        player = self.players[client]
        if player.name in self.cur_question.likes:
            return False
        if self.cur_question.lies.get(player.name) == liked:
            return False
        self.cur_question.likes[player.name] = liked
        for lier_name, lie in self.cur_question.lies.items():
            if lie == liked and lier_name != player.name:
                lp = self.get_player_by_name(lier_name)
                if lp:
                    lp.likecount += 1
        return True

    # ── Game state ────────────────────────────────────────────────────────────

    def get_gamestate(self):
        q = self.cur_question
        gs = {
            'state':         self.state,
            'players':       [],
            'question':      q.question        if q else '',
            'answer':        q.answer          if q else '',
            'author':       (q.author  or '')  if q else '',
            'flavor':       (q.flavor  or '')  if q else '',
            'currentlie':    self.currentlie,
            'phase_started': self.t,
            'phase_duration': {
                'pregame':      0,
                'lietome':      self.lietime,
                'lieselection': self.choicetime,
                'scoring':      self.scoretime,
                'finalscoring': self.scoretime * 2,
            }.get(self.state, 0),
            'round':        self.roundcount,
            'total_rounds': self.questionsperround,
            'paused':       self.paused,
        }
        for p in sorted(self.players.values(), key=lambda x: (-x.score, x.name)):
            pi = p.get_info()
            if q:
                pi.update(q.get_player_info(p.name))
            gs['players'].append(pi)
        return gs

    # ── State machine ─────────────────────────────────────────────────────────

    def handle_state(self, state):
        if state in self.states:
            return getattr(self, f'_handle_{state}')()

    def _handle_pregame(self):
        if self.forcestart:
            self.forcestart = False
            self.load_next_question()
            self.state = 'lietome'
            return 'all'

    def _handle_lietome(self):
        all_in = (len(self.players) > 0 and
                  len(self.cur_question.lies) == len(self.players))
        if all_in or (time.time() - self.t) >= self.lietime:
            self.time()
            self.state = 'lieselection'
            return 'all'

    def _handle_lieselection(self):
        all_voted = (len(self.players) > 0 and
                     len(self.cur_question.choices) >= len(self.players))
        if all_voted or (time.time() - self.t) >= self.choicetime:
            self.state = 'scoring'
            self.do_scoring()
            self.time()
            return 'all'

    def _handle_scoring(self):
        if (time.time() - self.t) >= self.scoretime:
            if self.roundcount >= self.questionsperround:
                self.state = 'finalscoring'
            else:
                self.forcestart = True
                self.state = 'pregame'
            self.time()
            return 'all'

    def _handle_finalscoring(self):
        if (time.time() - self.t) >= (2 * self.scoretime):
            self.reset()
            self.state = 'pregame'
            self.time()
            return 'all'


# ── Singleton game instance ───────────────────────────────────────────────────

game = Game()


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def update_view(recipients='all'):
    data = json.dumps(game.get_gamestate())
    targets = list(game.viewers)
    if recipients == 'all':
        targets += list(game.players.keys())
    for ws in targets:
        try:
            await ws.send_str(data)
        except Exception:
            pass


# ── Game tick ─────────────────────────────────────────────────────────────────

async def game_tick():
    while True:
        if not game.paused:
            result = game.handle_state(game.state)
            if result:
                await update_view(result)
        await asyncio.sleep(0.05)


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    game.clients.append(ws)
    print(f'WS connected: {request.remote}')

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                raw = msg.data
                if raw.startswith('Echo') or ':' not in raw:
                    continue
                command, _, parameter = raw.partition(':')
                update = False

                if command == 'loginname':
                    if game.add_player(ws, parameter):
                        update = 'viewers'

                elif command == 'forcestart':
                    if game.state == 'pregame':
                        game.forcestart = True

                elif command == 'view':
                    if ws not in game.viewers:
                        game.viewers.append(ws)
                    update = 'viewers'

                elif command == 'lie':
                    if game.state == 'lietome' and ws in game.players:
                        player = game.players[ws]
                        latinized = unidecode_allcaps_shorten32(parameter)
                        if player.name in game.cur_question.lies:
                            pass  # already submitted
                        elif is_too_close(latinized, game.cur_question.answer):
                            await ws.send_str('liereject:Too close to the real answer — try again!')
                        else:
                            game.cur_question.lies[player.name] = latinized
                            update = 'viewers'

                elif command == 'choice':
                    if game.lie_selection_received(ws, unidecode_allcaps_shorten32(parameter)):
                        update = 'viewers'

                elif command == 'like':
                    if game.like_recieved(ws, unidecode_allcaps_shorten32(parameter)):
                        update = 'viewers'

                elif command == 'pausegame':
                    game.paused = not game.paused
                    if not game.paused:
                        game.time()  # reset timer so remaining time is fair after resume
                    update = 'all'

                elif command == 'endgame':
                    game.reset()
                    game.state = 'pregame'
                    game.paused = False
                    game.time()
                    update = 'all'

                elif command == 'advancestate':
                    if game.state == 'pregame':
                        game.forcestart = True
                    elif game.state == 'lietome':
                        game.time()
                        game.state = 'lieselection'
                        update = 'all'
                    elif game.state == 'lieselection':
                        game.state = 'scoring'
                        game.do_scoring()
                        game.time()
                        update = 'all'
                    elif game.state == 'scoring':
                        if game.roundcount >= game.questionsperround:
                            game.state = 'finalscoring'
                        else:
                            game.forcestart = True
                            game.state = 'pregame'
                        game.time()
                        update = 'all'
                    elif game.state == 'finalscoring':
                        game.reset()
                        game.state = 'pregame'
                        game.time()
                        update = 'all'

                if update:
                    await update_view(update)

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break

    finally:
        print(f'WS disconnected: {request.remote}')
        game.remove_player(ws)

    return ws


# ── URL detection ─────────────────────────────────────────────────────────────

def get_public_urls():
    """Return (http_base_url, ws_url) for QR code and websocket_ip.js."""
    port = int(os.environ.get('PORT', 8000))

    # Render.com sets RENDER_EXTERNAL_URL (https://...)
    render_url = os.environ.get('RENDER_EXTERNAL_URL', '').rstrip('/')
    if render_url:
        ws = render_url.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws'
        return render_url, ws

    # Railway sets RAILWAY_PUBLIC_DOMAIN (no protocol)
    railway = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if railway:
        http = f'https://{railway}'
        ws   = f'wss://{railway}/ws'
        return http, ws

    # Generic PUBLIC_URL override
    pub = os.environ.get('PUBLIC_URL', '').rstrip('/')
    if pub:
        ws = pub.replace('https://', 'wss://').replace('http://', 'ws://') + '/ws'
        return pub, ws

    # Local — detect LAN IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = '127.0.0.1'
    return f'http://{ip}:{port}', f'ws://{ip}:{port}/ws'


# ── HTTP routes ───────────────────────────────────────────────────────────────

async def serve_ws_ip(request):
    """Serve auto-generated websocket_ip.js pointing at the correct WS URL."""
    _, ws_url = get_public_urls()
    js = (f'// Auto-generated by triviuhh.py\n'
          f'function get_websocket_ip() {{ return "{ws_url}"; }}\n')
    return web.Response(text=js, content_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})


async def static_handler(request):
    """Serve static files from the working directory."""
    path = request.match_info.get('path', 'index.html') or 'index.html'
    # Prevent directory traversal
    target = os.path.normpath(os.path.join(os.getcwd(), path))
    if not target.startswith(os.getcwd()):
        raise web.HTTPForbidden()
    if not os.path.isfile(target):
        raise web.HTTPNotFound()
    return web.FileResponse(target)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    port      = int(os.environ.get('PORT', 8000))
    http_url, _ = get_public_urls()

    # Generate QR code for the join URL
    try:
        qr = pyqrcode.create(http_url)
        qr.png('qrcode.png', scale=6)
        print(f'QR code -> {http_url}')
    except Exception as e:
        print(f'QR code error: {e}')

    # Load questions
    qfile = os.environ.get('QUESTIONS_FILE', 'questions2.csv')
    if os.path.exists(qfile):
        game.load_questions(qfile)
    else:
        print(f'Warning: {qfile} not found — no questions loaded')

    # Build aiohttp app
    @web.middleware
    async def frame_options_middleware(request, handler):
        response = await handler(request)
        # Allow any site to embed us in an iframe (needed for Google Sites)
        response.headers['X-Frame-Options'] = 'ALLOWALL'
        response.headers['Content-Security-Policy'] = "frame-ancestors *;"
        return response

    app = web.Application(middlewares=[frame_options_middleware])
    app.router.add_get('/ws',               ws_handler)
    app.router.add_get('/websocket_ip.js',  serve_ws_ip)
    app.router.add_get('/',                 lambda r: web.FileResponse('index.html'))
    app.router.add_get('/{path:.+}',        static_handler)

    # Start game tick
    asyncio.create_task(game_tick())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f'Triviuhh running -> {http_url}')
    await asyncio.Future()  # run forever


if __name__ == '__main__':
    asyncio.run(main())
