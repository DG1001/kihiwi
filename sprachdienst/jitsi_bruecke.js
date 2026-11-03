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

  // --- Avatar: der Professor aus avatar.js ---------------------------------
  // jitsi_bruecke.py setzt avatar.js vor diese Datei. Hier nur, was die Kachel
  // braucht: der Zustand vom Dienst und der gemessene Pegel der Sprachausgabe.
  let zustand = {};
  window.kiwiZustand = z => { zustand = z; };

  let bild = null;
  function bildspur() {
    if (!bild) {
      const c = document.createElement("canvas");
      c.width = 640; c.height = 360;
      const g = c.getContext("2d");
      const t0 = performance.now(), avatar = window.kiwiAvatar();
      // setInterval statt requestAnimationFrame: rAF steht still, sobald die
      // Seite nicht sichtbar ist -- und ohne Bildschirm ist sie das nie sicher.
      setInterval(() => avatar.kachel(g, performance.now() - t0, zustand, sprechpegel()), 40);
      avatar.kachel(g, 0, zustand, sprechpegel());
      bild = c.captureStream(25).getVideoTracks()[0];
    }
    return bild.clone();
  }
  window.kiwiAvatarProbe = (phase, pegel) => {
    // Nur fuer Tests: ein Einzelbild in einem Zustand, als PNG
    const c = document.createElement("canvas"); c.width = 640; c.height = 360;
    const avatar = window.kiwiAvatar();
    avatar.fest(pegel);
    avatar.kachel(c.getContext("2d"), 700, {phase, aufnahme: false}, sprechpegel());
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
