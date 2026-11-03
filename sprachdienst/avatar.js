// Kiwis Avatar: ein gezeichneter Professor. Gemeinsam fuer die Kachel der
// Jitsi-Bruecke (640x360) und die kleine Figur im Kopf von klient.html.
//
// Kein Portraet, der Typus "zerstreutes Genie" (Haarwolke, Brauen,
// Schnurrbart). Gezeichnet in 320x180-Koordinaten.
//   hoeren    : leichtes Nicken
//   antworten : Mund nach dem gemessenen Pegel der Sprachausgabe
//   denken    : Blick nach oben, Brauen hoch, drei Punkte
//   bereit    : ruhig, ab und zu Blinzeln
//   leerlauf  : Augen zu, abgedunkelt (Mikrofon aus)
//
// window.kiwiAvatar() liefert einen Zeichner mit eigenem Zustand (Mund,
// Blinzeln). pegel ist der RMS-Pegel der Sprachausgabe; null laesst den Mund,
// wie er ist.
(() => {
  const PHASEN = {leerlauf: "Mikrofon aus", bereit: "sag „Kiwi“",
                  hoeren: "hört zu", denken: "denkt nach", antworten: "spricht"};
  const HINTERGRUND = "#24331b";
  const HAUT = "#f2c7a0", OHR = "#e9b68d", HAAR = "#f3f3ee";
  const HAARWOLKE = [[112, 70, 22], [128, 48, 22], [152, 38, 22], [178, 40, 22],
                     [200, 54, 22], [212, 76, 20], [104, 94, 16], [218, 98, 16]];
  // Ausschnitt fuer die kleine Figur: Kopf mit Haarwolke und Denkpunkten
  const KLEIN = {x: 88, y: 12, b: 184, h: 147};

  window.kiwiAvatar = () => {
    let mund = 0, naechstesBlinzeln = 0, blinzelnBis = 0;

    function bewegen(t, pegel) {
      if (pegel !== null && pegel !== undefined) {
        // Mund: schnell auf, etwas langsamer zu -- wirkt wie Sprechen statt Flackern
        const ziel = Math.min(1, Math.max(0, (pegel - 0.01) * 9));
        mund = ziel > mund ? ziel : mund * 0.75 + ziel * 0.25;
      }
      if (t > naechstesBlinzeln) {
        blinzelnBis = t + 140;
        naechstesBlinzeln = t + 3000 + Math.random() * 2500;
      }
    }

    // Kopf und Denkpunkte in 320x180-Koordinaten, ohne Hintergrund
    function kopf(g, t, phase) {
      const augenZu = phase === "leerlauf" || t < blinzelnBis;
      const nach_oben = phase === "denken";
      const nicken = phase === "hoeren" ? Math.sin(t / 260) : 0;

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
    }

    return {
      // Die Kachel der Bruecke, 640x360: dazu der Zustand als Text und eine
      // laufende AUFNAHME als roter Balken. Im Labor steht sie gross auf dem
      // Bildschirm, in der Konferenz sieht man nur diese Kachel (§ 201 StGB,
      // siehe fachlich.md).
      kachel(g, t, zustand, pegel) {
        const phase = zustand.phase || "bereit";
        bewegen(t, pegel);
        g.save();
        g.scale(2, 2);
        g.fillStyle = HINTERGRUND; g.fillRect(0, 0, 320, 180);
        kopf(g, t, phase);
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
      },
      // Die kleine Figur: nur der Kopf, fuellt die ganze Leinwand. Ohne Text
      // und Aufnahmebalken -- die Seite zeigt beides selbst.
      klein(g, t, zustand, pegel) {
        const phase = zustand.phase || "bereit";
        bewegen(t, pegel);
        const w = g.canvas.width, h = g.canvas.height;
        g.save();
        g.fillStyle = HINTERGRUND; g.fillRect(0, 0, w, h);
        g.scale(w / KLEIN.b, h / KLEIN.h);
        g.translate(-KLEIN.x, -KLEIN.y);
        kopf(g, t, phase);
        g.restore();
        if (phase === "leerlauf") { g.fillStyle = "rgba(0, 0, 0, 0.45)"; g.fillRect(0, 0, w, h); }
      },
      // Fuer Pruefbilder: fester Mund, kein Blinzeln
      fest(m) { mund = m || 0; naechstesBlinzeln = Infinity; blinzelnBis = 0; },
    };
  };
})();
