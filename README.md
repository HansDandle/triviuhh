# Triviuhh - a LAN / online party trivia bluffing game

### How to play

1. Launch the server (defaults to `questions2.csv`, shuffled randomly each game):  
   ```
   python fakeage_server.py
   ```
2. **Host/Viewer** — open `http://[server-ip]:8000/host.html` on the TV/projector.  
   The host dashboard shows the QR code, player list, game state, and a **▶ START GAME** / **⏭ Next** button.
3. **Players** — scan the QR code or visit `http://[server-ip]:8000/` on their phones, enter a name, and tap **Play!**
4. The host presses **▶ START GAME** when everyone has joined, then **⏭ Next** to advance through rounds.

**Scoring:** 1 point for guessing the correct answer · 1 point each time another player picks your lie  
All answers are normalised to uppercase ASCII.


### Questions format

The server auto-detects the file type on the `--questions` flag (default: `questions2.csv`).

**CSV** (like `questions2.csv`): must have a header row with at least `Question` and `Answer` columns.  
Optional columns `Category` and `Sub_Category` are shown on the host dashboard as context.

**TSV** (like `questions.tsv`): tab-separated, one question per line:  
`[question]\t[answer]\t[author (optional)]\t[flavor (optional)]`

You can mix HTML in TSV questions for images/video:

```
What character is this? <br/><img src="img/myimage.jpg">	ANSWER
```

### To build or dev

Two main components:
- [fakeage_server.py](fakeage_server.py) — Python WebSocket + HTTP server
- [index.html](index.html) — Player UI
- [host.html](host.html) — **Host/viewer dashboard** (dark-mode, big-screen-friendly)

Install dependencies: `pip install -r requirements.txt`  
Standalone exe: `pyinstaller --onefile fakeage_server.py`

Have fun!

---

![game looks](screenshot.PNG)
