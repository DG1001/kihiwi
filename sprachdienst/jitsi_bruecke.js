// Kiwi-Bruecke, Seitenteil: laeuft in der Jitsi-Seite, BEVOR Jitsi laedt
// (Playwright add_init_script). Zwei Richtungen:
//
//   Raum -> Kiwi : jede eingehende Tonspur der Konferenz wird gemischt, auf
//                  16 kHz gebracht und in 512er-Bloecken int16 an Python
//                  gereicht (window.kiwiTonRaus) -- dasselbe Format wie /klient.
//   Kiwi -> Raum : was Kiwi sagt, kommt als PCM von Python (window.kiwiSpiel)
//                  und wird zum "Mikrofon" der Bruecke.
//
// Kiwis eigene Stimme hoert die Bruecke nicht wieder: Jitsi schickt einem
// Teilnehmer seine eigene Spur nicht zurueck.
(() => {
  const RATE = 16000, BLOCK = 512;

  // Gleicher Sender wie in klient.html: 512er-Bloecke als int16.
  const WORKLET = `
class Sender extends AudioWorkletProcessor {
  constructor() { super(); this.puffer = new Float32Array(${BLOCK}); this.n = 0; }
  process(eingang) {
    const k = eingang[0][0];
    if (!k) return true;
    for (let i = 0; i < k.length; i++) {
      this.puffer[this.n++] = k[i];
      if (this.n === ${BLOCK}) {
        const aus = new Int16Array(${BLOCK});
        for (let j = 0; j < ${BLOCK}; j++) {
          const v = Math.max(-1, Math.min(1, this.puffer[j]));
          aus[j] = v * 32767;
        }
        this.port.postMessage(aus.buffer, [aus.buffer]);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor('sender', Sender);
`;

  function nachBase64(puffer) {
    const b = new Uint8Array(puffer);
    let s = "";
    for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s);
  }
  function ausBase64(text) {
    const s = atob(text), b = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
    return b.buffer;
  }

  // --- Kiwi -> Raum: Sprachausgabe als Mikrofonspur -------------------------
  let ausCtx = null, ausZiel = null, pegelMesser = null;
  let spielt = 0, warAn = false, tonRate = 22050;
  function ausgang() {
    if (!ausCtx) {
      ausCtx = new AudioContext({sampleRate: 48000});
      ausZiel = ausCtx.createMediaStreamDestination();
      // Die Sprachausgabe laeuft zusaetzlich durch einen Messer: daraus
      // bewegt der Avatar den Mund -- nach dem, was tatsaechlich klingt.
      pegelMesser = ausCtx.createAnalyser();
      pegelMesser.fftSize = 512;
    }
    if (ausCtx.state === "suspended") ausCtx.resume();
    return ausZiel;
  }

  const messpuffer = new Float32Array(512);
  function sprechpegel() {
    if (!pegelMesser) return 0;
    pegelMesser.getFloatTimeDomainData(messpuffer);
    let q = 0;
    for (const v of messpuffer) q += v * v;
    return Math.sqrt(q / messpuffer.length);
  }

  window.kiwiRate = r => { tonRate = r; };
  window.kiwiSpiel = text => {
    const ziel = ausgang();
    const roh = new Int16Array(ausBase64(text));
    const b = ausCtx.createBuffer(1, roh.length, tonRate);
    const k = b.getChannelData(0);
    for (let i = 0; i < roh.length; i++) k[i] = roh[i] / 32768;
    const q = ausCtx.createBufferSource();
    q.buffer = b; q.connect(ziel); q.connect(pegelMesser);
    // Wie in klient.html: nur zuruecksetzen, wenn die Warteschlange leer ist,
    // sonst spielen zwei Stuecke uebereinander.
    const jetzt = ausCtx.currentTime;
    if (spielt < jetzt || !warAn) { spielt = jetzt + 0.02; warAn = true; }
    q.start(spielt);
    spielt += b.duration;
  };

  // --- Avatar: ein gezeichneter Professor ---------------------------------
  // Kein Portraet, der Typus "zerstreutes Genie" (Haarwolke, Brauen,
  // Schnurrbart). Gezeichnet in 320x180-Koordinaten, doppelt skaliert.
  //   hoeren    : leichtes Nicken
  //   antworten : Mund nach dem gemessenen Pegel der Sprachausgabe
  //   denken    : Blick nach oben, Brauen hoch, drei Punkte
  //   bereit    : ruhig, ab und zu Blinzeln
  //   leerlauf  : Augen zu, abgedunkelt (Mikrofon aus)
  // Dazu der Zustand als Text und eine laufende AUFNAHME als roter Balken:
  // Im Labor steht sie gross auf dem Bildschirm, in der Konferenz sieht man
  // nur diese Kachel (§ 201 StGB, siehe fachlich.md).
  const PHASEN = {leerlauf: "Mikrofon aus", bereit: "sag „Kiwi“",
                  hoeren: "hört zu", denken: "denkt nach", antworten: "spricht"};
  let zustand = {};
  window.kiwiZustand = z => { zustand = z; };

  const HAUT = "#f2c7a0", OHR = "#e9b68d", HAAR = "#f3f3ee";
  const HAARWOLKE = [[112, 70, 22], [128, 48, 22], [152, 38, 22], [178, 40, 22],
                     [200, 54, 22], [212, 76, 20], [104, 94, 16], [218, 98, 16]];
  let mund = 0, naechstesBlinzeln = 0, blinzelnBis = 0;

  function professor(g, t) {
    const phase = zustand.phase || "bereit";
    // Mund: schnell auf, etwas langsamer zu -- wirkt wie Sprechen statt Flackern
    const ziel = Math.min(1, Math.max(0, (sprechpegel() - 0.01) * 9));
    mund = ziel > mund ? ziel : mund * 0.75 + ziel * 0.25;

    if (t > naechstesBlinzeln) { blinzelnBis = t + 140; naechstesBlinzeln = t + 3000 + Math.random() * 2500; }
    const augenZu = phase === "leerlauf" || t < blinzelnBis;
    const nach_oben = phase === "denken";
    const nicken = phase === "hoeren" ? Math.sin(t / 260) : 0;

    g.save();
    g.scale(2, 2);
    g.fillStyle = "#24331b"; g.fillRect(0, 0, 320, 180);

    g.save();
    g.translate(160, 170);
    g.rotate(nicken * 0.025);
    g.translate(-160, -170 + nicken * 2.5);

    g.fillStyle = HAAR;
    for (const [x, y, r] of HAARWOLKE) { g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI); g.fill(); }
    g.fillStyle = OHR;
    g.beginPath(); g.ellipse(117, 104, 9, 13, 0, 0, 2 * Math.PI); g.fill();
    g.beginPath(); g.ellipse(203, 104, 9, 13, 0, 0, 2 * Math.PI); g.fill();
    g.fillStyle = HAUT;
    g.beginPath(); g.ellipse(160, 100, 44, 52, 0, 0, 2 * Math.PI); g.fill();

    g.lineCap = "round";
    g.strokeStyle = "#d9a47c"; g.lineWidth = 2;
    g.beginPath(); g.moveTo(138, 72); g.quadraticCurveTo(160, 66, 182, 72);
    g.moveTo(142, 80); g.quadraticCurveTo(160, 75, 178, 80); g.stroke();

    const hoch = nach_oben ? -4 : 0;
    g.strokeStyle = HAAR; g.lineWidth = 6;
    g.beginPath(); g.moveTo(130, 88 + hoch); g.quadraticCurveTo(142, 78 + hoch, 154, 86 + hoch);
    g.moveTo(166, 86 + hoch); g.quadraticCurveTo(178, 78 + hoch, 190, 88 + hoch); g.stroke();

    if (augenZu) {
      g.strokeStyle = "#2c2420"; g.lineWidth = 2.5;
      g.beginPath(); g.moveTo(137, 100); g.quadraticCurveTo(143, 104, 149, 100);
      g.moveTo(171, 100); g.quadraticCurveTo(177, 104, 183, 100); g.stroke();
    } else {
      const dy = nach_oben ? -3 : 0, dx = nach_oben ? 2 : 0;
      g.fillStyle = "#2c2420";
      g.beginPath(); g.ellipse(143 + dx, 99 + dy, 5, 6, 0, 0, 2 * Math.PI);
      g.ellipse(177 + dx, 99 + dy, 5, 6, 0, 0, 2 * Math.PI); g.fill();
      g.fillStyle = "#fff";
      g.beginPath(); g.arc(145 + dx, 97 + dy, 1.6, 0, 2 * Math.PI);
      g.arc(179 + dx, 97 + dy, 1.6, 0, 2 * Math.PI); g.fill();
    }

    g.strokeStyle = "#c98f67"; g.lineWidth = 3;
    g.beginPath(); g.moveTo(160, 102); g.quadraticCurveTo(153, 116, 160, 120);
    g.quadraticCurveTo(165, 121, 167, 118); g.stroke();

    g.fillStyle = "#7a2e2e";
    g.beginPath(); g.ellipse(160, 135, 11, 1 + 7 * mund, 0, 0, 2 * Math.PI); g.fill();

    g.fillStyle = HAAR;
    g.beginPath(); g.moveTo(128, 126);
    g.quadraticCurveTo(144, 114, 160, 122); g.quadraticCurveTo(176, 114, 192, 126);
    g.quadraticCurveTo(178, 136, 160, 128); g.quadraticCurveTo(142, 136, 128, 126);
    g.fill();
    g.restore();

    if (phase === "denken") {
      const welcher = Math.floor(t / 350) % 4;
      [[236, 64, 4], [248, 52, 5], [262, 40, 6]].forEach(([x, y, r], i) => {
        g.fillStyle = i < welcher ? "#d8e8c8" : "rgba(216, 232, 200, 0.25)";
        g.beginPath(); g.arc(x, y, r, 0, 2 * Math.PI); g.fill();
      });
    }
    if (phase === "leerlauf") { g.fillStyle = "rgba(0, 0, 0, 0.35)"; g.fillRect(0, 0, 320, 180); }

    g.textAlign = "center"; g.textBaseline = "middle";
    g.fillStyle = "#d8e8c8"; g.font = "12px sans-serif";
    g.fillText(PHASEN[phase] || "", 160, 172);
    if (zustand.aufnahme) {
      g.fillStyle = "#c62828"; g.fillRect(0, 0, 320, 26);
      g.fillStyle = "#fff"; g.font = "bold 16px sans-serif";
      g.fillText("● AUFNAHME", 160, 14);
    }
    g.restore();
  }

  let bild = null;
  function bildspur() {
    if (!bild) {
      const c = document.createElement("canvas");
      c.width = 640; c.height = 360;
      const g = c.getContext("2d");
      const t0 = performance.now();
      // setInterval statt requestAnimationFrame: rAF steht still, sobald die
      // Seite nicht sichtbar ist -- und ohne Bildschirm ist sie das nie sicher.
      setInterval(() => professor(g, performance.now() - t0), 40);
      professor(g, 0);
      bild = c.captureStream(25).getVideoTracks()[0];
    }
    return bild.clone();
  }
  window.kiwiAvatarProbe = (phase, pegel) => {
    // Nur fuer Tests: ein Einzelbild in einem Zustand, als PNG
    const c = document.createElement("canvas"); c.width = 640; c.height = 360;
    const vorher = [zustand, mund, naechstesBlinzeln, blinzelnBis];
    zustand = {phase, aufnahme: false};
    mund = pegel || 0; naechstesBlinzeln = Infinity; blinzelnBis = 0;
    professor(c.getContext("2d"), 700);
    [zustand, mund, naechstesBlinzeln, blinzelnBis] = vorher;
    return c.toDataURL("image/png");
  };

  // Jitsi fragt Mikrofon und Kamera ueber getUserMedia an: es bekommt Kiwis
  // Stimme und das Kachelbild statt echter Geraete.
  navigator.mediaDevices.getUserMedia = async c => {
    const spuren = [];
    if (c && c.audio) spuren.push(ausgang().stream.getAudioTracks()[0].clone());
    if (c && c.video) spuren.push(bildspur());
    return new MediaStream(spuren);
  };

  // --- Raum -> Kiwi: alle eingehenden Tonspuren mischen ---------------------
  let einCtx = null, mischer = null, bereit = null;
  function eingang() {
    if (!bereit) bereit = (async () => {
      einCtx = new AudioContext({sampleRate: RATE});
      await einCtx.audioWorklet.addModule(URL.createObjectURL(
        new Blob([WORKLET], {type: "application/javascript"})));
      mischer = einCtx.createGain();
      const knoten = new AudioWorkletNode(einCtx, "sender");
      knoten.port.onmessage = e => {
        if (window.kiwiTonRaus) window.kiwiTonRaus(nachBase64(e.data));
      };
      mischer.connect(knoten);
    })();
    return bereit.then(() => { if (einCtx.state === "suspended") einCtx.resume(); });
  }

  const angehaengt = new Map();
  async function anhaengen(spur) {
    if (spur.kind !== "audio" || angehaengt.has(spur.id)) return;
    angehaengt.set(spur.id, null);
    await eingang();
    const quelle = einCtx.createMediaStreamSource(new MediaStream([spur]));
    quelle.connect(mischer);
    angehaengt.set(spur.id, quelle);
    spur.addEventListener("ended", () => {
      quelle.disconnect();
      angehaengt.delete(spur.id);
    });
  }

  const EchtePC = window.RTCPeerConnection;
  function KiwiPC(...a) {
    const pc = new EchtePC(...a);
    pc.addEventListener("track", e => { anhaengen(e.track); });
    return pc;
  }
  KiwiPC.prototype = EchtePC.prototype;
  Object.setPrototypeOf(KiwiPC, EchtePC);  // statische Methoden (generateCertificate)
  window.RTCPeerConnection = KiwiPC;

  // Jitsi startet Kiwi stumm und ohne Kamera (Mikrofon durchgestrichen,
  // Buchstabe statt Kachelbild), obwohl beide Spuren laufen -- dann sieht
  // niemand den Aufnahme-Balken. Einmal nach dem Beitritt beides einschalten,
  // nur einmal, damit ein Moderator Kiwi danach weiter stumm schalten kann.
  let entstummt = false;
  window.kiwiEntstummen = () => {
    if (entstummt || typeof APP === "undefined" || !APP.conference
        || !APP.conference.isJoined || !APP.conference.isJoined()) return null;
    entstummt = true;
    const war = {ton: APP.conference.isLocalAudioMuted(),
                 bild: APP.conference.isLocalVideoMuted()};
    if (war.ton) APP.conference.muteAudio(false);
    if (war.bild) APP.conference.muteVideo(false);
    return war;
  };

  window.kiwiDrin = () => typeof APP !== "undefined" && !!APP.conference
                          && !!APP.conference.isJoined && APP.conference.isJoined();

  // Wie viele ausser Kiwi in der Konferenz sind (wer in der Lobby wartet,
  // zaehlt nicht). null, wenn es sich nicht feststellen laesst -- dann bleibt
  // Kiwi lieber, statt grundlos zu gehen.
  window.kiwiAndere = () => {
    if (!window.kiwiDrin()) return null;
    try {
      const n = APP.conference.membersCount;
      if (typeof n === "number") return n - 1;
      return APP.conference._room.getParticipants().length;
    } catch (e) {
      return null;
    }
  };

  window.kiwiStand = () => ({spuren: angehaengt.size,
                             andere: window.kiwiAndere(),
                             eingang: einCtx ? einCtx.state : null,
                             ausgang: ausCtx ? ausCtx.state : null,
                             stumm: (typeof APP !== "undefined" && APP.conference
                                     && APP.conference.isLocalAudioMuted)
                                    ? APP.conference.isLocalAudioMuted() : null,
                             kamera_aus: (typeof APP !== "undefined" && APP.conference
                                          && APP.conference.isLocalVideoMuted)
                                         ? APP.conference.isLocalVideoMuted() : null});
})();
