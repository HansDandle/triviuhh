# Triviuhh

A multiplayer party trivia-bluffing game playable over LAN or the internet. Players are given a trivia question and must write a convincing fake answer — then try to guess the real one from all the lies. Points are awarded for guessing correctly and for fooling other players.

Inspired by / based on the original work by [@Beherith](https://github.com/Beherith/fakeage) — thank you for the foundation!

---

## Live demo

**[https://triviuhh.onrender.com](https://triviuhh.onrender.com)**  
*(free tier — may take ~30s to wake up)*

---

## How to play

### Players (phones)
1. Visit the game URL or scan the QR code shown on the host screen.
2. Enter your name and tap **Play!**
3. Each round:
   - **Lie phase** — read the question and type a convincing fake answer.
   - **Vote phase** — all answers (real + lies) are shown. Pick the one you think is true.
   - **Reveal phase** — see who wrote which lie, who was fooled, and who guessed right.
4. After all rounds, final scores are shown.

### Host (TV / projector)
1. Open `host.html` and enter the host PIN.
2. Wait for players to join, then press **▶ START GAME**.
3. Use **⏭ Skip** to advance a phase early, **⏸ Pause** to freeze the timer mid-round, and **⏹ End Game** to reset to the lobby.
4. During the reveal, use **✔ Accept as correct** on any lie card to retroactively award points if a player's answer was technically right.

---

## Scoring

| Event | Points |
|---|---|
| Guessing the correct answer | +1 |
| Another player picks your lie | +1 per player fooled |
| Host marks your lie as correct | +1 (retroactive) |

All answers are normalised to uppercase ASCII. The server fuzzy-matches submitted lies against the real answer (substring, word overlap, and character similarity) to prevent players submitting the answer as their lie.

---

## Running locally

```bash
pip install -r requirements.txt
python triviuhh.py
```

Then open `http://localhost:8000` on player phones and `http://localhost:8000/host.html` on the host screen.

The server auto-detects your LAN IP and generates a QR code (`qrcode.png`) pointing to it.

---

## Deployment (Render.com / Railway)

`triviuhh.py` is a single-port [aiohttp](https://docs.aiohttp.org/) server that serves static files over HTTP and handles the game over WebSocket at `/ws`. It auto-detects `RENDER_EXTERNAL_URL`, `RAILWAY_PUBLIC_DOMAIN`, or `PUBLIC_URL` environment variables to generate the correct WebSocket URL for clients.

A `render.yaml` and `Procfile` are included for one-click deploy.

---

## Questions format

The server loads `questions2.csv` by default (set `QUESTIONS_FILE` env var to override).

**CSV** — must have a header row with at least `Question` and `Answer` columns.  
Optional: `Category` and `Sub_Category` are displayed on the host dashboard as context clues.

```csv
Question,Answer,Category,Sub_Category
What is the speed of light?,299792458 M/S,Science,Physics
```

**TSV** — tab-separated, one question per line:
```
Question\tAnswer\tCategory (optional)\tFlavour (optional)
```

---

## Project structure

| File | Purpose |
|---|---|
| `triviuhh.py` | Production server — aiohttp, HTTP + WebSocket on one port |
| `fakeage_server_ws3.py` | Legacy async server (websockets library) |
| `fakeage_server.py` | Legacy server (SimpleWebSocketServer) |
| `index.html` | Player phone UI (jQuery Mobile, dark theme) |
| `host.html` | Host/TV dashboard (vanilla JS, dark mode) |
| `questions2.csv` | 30 000+ trivia questions |

---

## Credits

Original game concept and server by **Peter Sárközy (Beherith)**:  
[https://github.com/Beherith/fakeage](https://github.com/Beherith/fakeage)

Triviuhh is a heavily extended fork — rebranded UI, dark theme, online deployment, timer-authoritative game flow, host controls, fuzzy answer matching, auto-reconnect, and more.
